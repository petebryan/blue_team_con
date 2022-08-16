# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
"""
run_notebook - script for unattended notebook execution.

Requirements:
- azure_cli logon
- msticpyconfig.yaml

Steps:
- run notebook based on input YAML
- OR watch folder for files
- read parameters
- create output notebook name/path
- run notebook
- inspect notebook for result
  - if finding
    - save copy to findings folder
    - save html copy

Incrontab
https://linux.die.net/man/5/incrontab

"""
import argparse
import logging
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import shutil
from time import sleep
from typing import Any, Dict, Union

import papermill as pm
import scrapbook as sb
import yaml
from nbconvert import HTMLExporter, PythonExporter


_LOG_FILE_NAME = "run_notebook.log"


@dataclass
class NotebookParams:
    """
    Notebook execution parameters.

    Notes
    -----
    The parameters file is a YAML file with 2 main sections:
    notebook and papermill.
    The notebook section must contain the name of the notebook
    to run (relative to the nb-path root).
    Parameters in the "notebook" section will be passed to papermill's
    exec_notebook function. Valid entries include:

    - request_save_on_cell_execute (bool, optional) - Request save notebook
      after each cell execution
    - autosave_cell_every (int, optional) - How often in seconds to save in the
      middle of long cell executions
    - prepare_only (bool, optional) - Flag to determine if execution should occur or not
    - kernel_name (str, optional) - Name of kernel to execute the notebook against
    - language (str, optional) - Programming language of the notebook
    - progress_bar (bool, optional) - Flag for whether or not to show the progress bar.
    - log_output (bool, optional) - Flag for whether or not to write notebook output to
      the configured logger
    - start_timeout (int, optional) - Duration in seconds to wait for kernel start-up
    - report_mode (bool, optional) - Flag for whether or not to hide input.
    - cwd (str or Path, optional) - Working directory to use when executing the notebook

    """

    papermill: Dict[str, Any]
    exec_params: Dict[str, Any]
    identifier: str
    source_file: Path
    working_file: Path
    job_id: str

    @property
    def notebook(self):
        """Return the name of the notebook to run."""
        return self.exec_params.get("notebook")

    @property
    def kernel(self):
        """Return name of the kernel to use."""
        return self.exec_params.get("kernel")

    @property
    def language(self):
        """Return language of notebook job."""
        return self.exec_params.get("language", "python")


class NotebookJob:
    """Notebook Job class."""

    def __init__(self, global_args, job_file):
        """Initialize the notebook job."""
        self._global_args = global_args
        self.start_time = datetime.now(timezone.utc)
        self.job_id = str(uuid.uuid4())
        self.nb_path = global_args.nb_path
        self.output_path = self._get_output_folder_path(
            global_args.output_path, global_args.output_div
        )
        self.queue_path = global_args.queue_path
        self.nb_params = self.read_params(job_file)
        self.job_file = job_file
        self.nb = None
        _validate_params(global_args, self.nb_params)

    def run(self):
        """Run the job."""
        self.log_info(
            f"Job run started: {self.input_notebook} ({self.output_notebook})."
        )
        self.nb = _run_notebook(
            self.input_file_path,
            self.output_file_path,
            self.nb_params,
        )
        self._check_for_findings()
        Path(self.nb_params.working_file).rename(
            Path(self.nb_params.working_file)
            .parent.joinpath(self.output_notebook)
            .with_suffix(".job")
        )

        self.log_info(
            f"Job run complete: {self.input_notebook} ({self.output_notebook})."
        )

    @property
    def input_file_path(self):
        """Return name of notebook to execute."""
        return Path(self.nb_path).joinpath(self.input_notebook)

    @property
    def output_file_path(self):
        """Return name of notebook to execute."""
        return Path(self.output_path).joinpath(self.output_notebook)

    @property
    def input_notebook(self):
        """Return name of notebook to execute."""
        return self.nb_params.notebook

    @property
    def output_notebook(self):
        """Return an output filename."""
        notebook_name = Path(self.nb_params.notebook).stem
        return f"{notebook_name}-{self.nb_params.identifier}-{self.job_time}.ipynb"

    @property
    def job_time(self):
        """Return current time formatted for filename compatibility."""
        return re.sub("[:.]", "-", self.start_time.isoformat())

    def read_params(self, file_path) -> NotebookParams:
        """Read the parameters file and return PM and exec parameters."""
        working_file = Path(file_path).with_name(f"{self.job_id}.tmp")
        Path(file_path).rename(working_file)
        yaml_text = Path(working_file).read_text(encoding="utf-8")
        params = yaml.safe_load(yaml_text)

        papermill_params = params.get("papermill")
        exec_params = params.get("exec", {})
        identifiers = exec_params.get("identifier", "")
        if isinstance(identifiers, str):
            identifiers = [identifiers]
        identifier = "-".join(papermill_params.get(param, "") for param in identifiers)
        identifier = _safe_file_name(identifier.replace("--", "-"))

        return NotebookParams(
            papermill=papermill_params,
            exec_params=exec_params,
            identifier=identifier,
            source_file=Path(file_path),
            working_file=working_file,
            job_id=self.job_id,
        )

    def _get_output_folder_path(self, root: Union[str, Path], division: str):
        date_parts = {
            "y": self.start_time.strftime("%Y"),
            "m": self.start_time.strftime("%m"),
            "d": self.start_time.strftime("%d"),
            "h": self.start_time.strftime("%H"),
        }

        folder_path = []
        for part, date_str in date_parts.items():
            folder_path.append(date_str)
            if division.casefold() == part:
                break

        out_path = Path(root).joinpath("/".join(folder_path))
        if not out_path.is_dir():
            out_path.mkdir(parents=True)
        return out_path

    def _check_for_findings(self):
        nb = sb.read_notebook(str(self.output_file_path))
        if nb.scraps.get("Findings"):
            self.log_info("Notebook has findings.")
            findings_folder = Path(self._global_args.findings_path)
            if not findings_folder.is_dir():
                findings_folder.mkdir(parents=True)
            shutil.copyfile(self.output_file_path, findings_folder)
            self.log_info("Creating html copy in {findings_folder}.")
            _notebook_to_html(Path(findings_folder).joinpath(self.output_file_path))

    def log_info(self, message):
        """Log an information message."""
        logging.info("%s - %s", self.job_id, message)


def main(global_args):
    """Run main loop for NB processing."""
    _start_logging(global_args.log_path)
    logging.info("====================")
    logging.info("run_notebook started")
    _watch_for_jobs(global_args)
    logging.info("run_notebook ended")


def create_folders(global_args):
    """Create folders for notebook runs."""
    Path(global_args.nb_path).mkdir(parents=True, exist_ok=True)
    Path(global_args.log_path).mkdir(parents=True, exist_ok=True)
    Path(global_args.output_path).mkdir(parents=True, exist_ok=True)
    Path(global_args.queue_path).mkdir(parents=True, exist_ok=True)
    Path(global_args.findings_path).mkdir(parents=True, exist_ok=True)
    Path(global_args.config_path).mkdir(parents=True, exist_ok=True)


def _start_logging(log_path: Union[str, Path]):
    """Start logging to file."""
    if log_path is not None:
        if not Path(log_path).is_dir():
            Path(log_path).mkdir(parents=True)
        log_file = Path(log_path).joinpath(_LOG_FILE_NAME)
    else:
        log_file = None
    logging.basicConfig(
        filename=log_file,
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s: %(levelname)s - %(message)s",
    )


def _watch_for_jobs(global_args):
    """Enter a loop looking for jobs to execute."""
    queue_folder = Path(global_args.queue_path)

    while True:
        jobs = list(queue_folder.glob("*.yaml"))
        for job in jobs:
            if not job.is_file():
                continue
            logging.info("Job created")
            nb_job = NotebookJob(global_args, job)
            try:
                nb_job.run()
            except KeyboardInterrupt:
                logging.info("Shutdown requested")
                break
            except Exception as err:  # pylint: disable=broad-except
                logging.error(
                    "Exception while running job (notebook %s, job %s)",
                    nb_job.input_notebook,
                    nb_job.job_id,
                    exc_info=err,
                )
            logging.info("Job complete")
        logging.info("Waiting for jobs %d sec", global_args.check_interval)
        sleep(global_args.check_interval)


_PARAM_VALIDATION = [
    ("exec_params", None, dict, "is-type"),
    ("exec_params", None, dict, "not-empty"),
    ("exec_params", "notebook", str, "path-exists"),
    ("exec_params", "identifier", str, "key-exists"),
    ("papermill", None, dict, "is-type"),
    ("papermill", None, dict, "not-empty"),
]


def _validate_params(global_args, nb_params: NotebookParams):
    """Validate contents of parameters file."""
    param_dict = asdict(nb_params)
    for section, item, data_type, check in _PARAM_VALIDATION:
        try:
            test_val = (
                param_dict.get(section, {}).get(item)
                if item
                else param_dict.get(section)
            )

            if not isinstance(test_val, data_type):
                raise TypeError(
                    f"Failed check {section}/{item}: is not expected type {data_type}."
                )

            if check == "path-exists":
                if not Path(global_args.nb_path).joinpath(test_val):
                    raise ValueError(f"Failed check {section}/{item}: {check}")
            elif check == "key-exists":
                id_list = [test_val] if isinstance(test_val, str) else test_val
                for ident in id_list:
                    if not param_dict.get("papermill", {}).get(ident):
                        raise ValueError(f"Failed check {section}/{item}: {check}")
            elif check == "not-empty":
                if not test_val:
                    raise ValueError(f"Failed check {section}/{item}: {check}")
        except KeyError:
            logging.error(
                "Validation check on parameters - missing %s.%s", section, item
            )
            raise
        except (ValueError, TypeError) as valid_err:
            logging.error("\n".join(valid_err.args))
            raise


def _safe_file_name(input_name):
    """Return filename with illegal characters replaced with '-'."""
    return re.sub(r"[<>:\"/\\|?*]", "-", input_name)


# papermill.execute.execute_notebook(
#   input_path, output_path, parameters=None, engine_name=None, request_save_on_cell_execute=True, prepare_only=False, kernel_name=None, language=None, progress_bar=True, log_output=False, stdout_file=None, stderr_file=None, start_timeout=60, report_mode=False, cwd=None, **engine_kwargs
# )
# Executes a single notebook locally.

# PARAMETERS
# input_path (str or Path) - Path to input notebook
# output_path (str or Path or None) - Path to save executed notebook. If None, no file will be saved
# parameters (dict, optional) - Arbitrary keyword arguments to pass to the notebook parameters
# engine_name (str, optional) - Name of execution engine to use
# request_save_on_cell_execute (bool, optional) - Request save notebook after each cell execution
# autosave_cell_every (int, optional) - How often in seconds to save in the middle of long cell executions
# prepare_only (bool, optional) - Flag to determine if execution should occur or not
# kernel_name (str, optional) - Name of kernel to execute the notebook against
# language (str, optional) - Programming language of the notebook
# progress_bar (bool, optional) - Flag for whether or not to show the progress bar.
# log_output (bool, optional) - Flag for whether or not to write notebook output to the configured logger
# start_timeout (int, optional) - Duration in seconds to wait for kernel start-up
# report_mode (bool, optional) - Flag for whether or not to hide input.
# cwd (str or Path, optional) - Working directory to use when executing the notebook
# **kwargs - Arbitrary keyword arguments to pass to the notebook engine

# RETURNS
# nb - Executed notebook object

# RETURN TYPE
# NotebookNode
_PM_EXEC_ARGS = [
    "engine_name",
    "request_save_on_cell_execute",
    "autosave_cell_every",
    "kernel_name",
    "language",
    "progress_bar",
    "log_output",
    "report_mode",
]


def _run_notebook(
    input_nb: Union[str, Path],
    output_nb: Union[str, Path],
    nb_params: NotebookParams,
):
    """Execute the notebook."""
    nb_kwargs = {
        key: val for key, val in nb_params.exec_params.items() if key in _PM_EXEC_ARGS
    }
    return pm.execute_notebook(
        input_path=input_nb,
        output_path=output_nb,
        parameters=nb_params.papermill,   # Python dict
        **nb_kwargs,
    )


def _notebook_to_html(nb_path):
    """Convert a notebook to HTML."""
    # Instantiate the exporter
    html_exporter = HTMLExporter()
    html_exporter.template_name = "classic"

    # Convert the notebook
    body, _ = html_exporter.from_notebook_node(nb_path)

    out_file = Path(nb_path).with_suffix(".html")
    with open(out_file, "w", encoding="utf-8") as nb_file:
        nb_file.write(body)


def _add_script_args(description):
    parser = argparse.ArgumentParser(
        description=description, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "cmd",
        choices=["config", "run"],
        help="\n".join(
            [
                "Run command: [setup | run]",
                "setup - create folders for runs."
                "run - monitor queue folder for files to run."
            ]
        ),
    )
    parser.add_argument(
        "--nb-path",
        "-n",
        required=False,
        help="Path to input notebooks.",
        default="./nb",
    )
    parser.add_argument(
        "--log-path",
        "-l",
        required=False,
        help="Path to root folder for output.",
        default=None,
    )
    parser.add_argument(
        "--output-path",
        "-o",
        required=False,
        help="Path to root folder for output.",
        default="./output",
    )
    parser.add_argument(
        "--queue-path",
        "-q",
        required=False,
        help="Path to the input queue.",
        default="./queue",
    )
    parser.add_argument(
        "--output-div",
        "-d",
        help="Time division for output folders (h, d, m, y).",
        default="d",
    )
    parser.add_argument(
        "--findings-path",
        "-f",
        help=("Path to root of findings store."),
        default="./findings",
    )
    parser.add_argument(
        "--check-interval",
        "-i",
        help=("Number of seconds to sleep between checks."),
        default=3.0,
    )
    parser.add_argument(
        "--msticpy-config",
        "-m",
        help=("path to msticpyconfig.yaml to use for notebook runs."),
    )
    parser.add_argument(
        "--config-path",
        "-c",
        help=("path to configuration folder."),
        default="./config",
    )
    return parser


# pylint: disable=invalid-name
if __name__ == "__main__":
    arg_parser = _add_script_args(description=__doc__)
    script_args = arg_parser.parse_args()

    if script_args.cmd == "run":
        main(script_args)
    if script_args.cmd == "config":
        create_folders(script_args)
