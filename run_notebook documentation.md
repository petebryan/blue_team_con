run_notebook documentation
==========================

Setup
-----

Create folder structure for notebook input/output. These can be
distributed in different locations but easier if they are all in
the same structure.

The name of the root folder can be changed.

```bash
nbexec
├───config
├───log
├───nb
├───output
└───queue
```

config - holds MSTICPy config file and related data.
log - holds log files.
nb - holds source notebooks - this can contain multiple folders
output - stores executed notebook results
queue - folder for input files with parameters to executed notebooks.

Docker
------

Dockerfile
^^^^^^^^^^

```dockerfile
FROM continuumio/miniconda3
# installing Msticpy requirements and dependencies
RUN pip install azure-cli
RUN pip install --upgrade msticpy[all]
RUN pip install papermill scrapbook
```

Build Docker image
^^^^^^^^^^^^^^^^^^

```bash
docker build --pull --rm -f "e:/src/blue_team_con\Dockerfile.txt" -t blueteamcon:latest "e:/src/blue_team_con"

```

Run Docker container
^^^^^^^^^^^^^^^^^^^^

```bash
docker run -it --rm -v e:/src/blue_team_con/nbexec:/nbexec -w /nbexec -e MSTICPYCONFIG='/nbexec/config/msticpyconfig.yaml' blueteamcon:latest bash
```

Docker run Switches:

- -it switches tell docker to run the container in interactive mode
- -rm shuts down the contain when the main command (in this case bash) exits
- -v Mounts the source folder in the container on the mountpoint /nbexec
- -w Set /nbexec as the current directory
- -e Set MSTICPYCONFIG environment variable
- blueteamcon:latest - the docker image to use
- bash run bash at start up

Job Parameters File
-------------------

A parameters file contains the instructions to run a single notebook.

The format is as follows:

```yml
exec:
  notebook: ip_explorer.ipynb
  identifier: ip_address
papermill:
  ip_address: 85.214.149.236
  start: "2022-08-01T17:00"
  end: "2022-08-01T17:00"
```

The `exec` section tells run_notebook, which notebook to run using the
notebook key. This should
be a path to a notebook (.ipynb) file in the nb folder (or subfolder).

The `identifier` item tells run_notebook which item or items in the `papermill`
section should be used to create the output notebook file name. This must
be one or more (`identifier` can be a list of parameter names).
In the example above, the value of ip_address will be included in the output file.

You can include addition key/pair parameters here that will be passed as
kwargs to the papermill.execute_notebook call (e.g. specifying a particular
kernel to use).

The `papermill` section contains the parameters that will be passed to the
notebook. In the above example it will pass an IP Address and start and end
dates.

Running the run_notebook.py
---------------------------

You must copy the run_notebook.py file to the root of your folder structure
(nbexec in the examples above).

```bash
python -m run_notebook
```

Optional arguments
^^^^^^^^^^^^^^^^^^

```
  -h, --help            show this help message and exit
  --nb-path NB_PATH, -n NB_PATH
                        Path to input notebooks.
  --log-path LOG_PATH, -l LOG_PATH
                        Path to root folder for output.
  --output-path OUTPUT_PATH, -o OUTPUT_PATH
                        Path to root folder for output.
  --queue-path QUEUE_PATH, -q QUEUE_PATH
                        Path to the input queue.
  --output-div OUTPUT_DIV, -d OUTPUT_DIV
                        Time division for output folders (h, d, m, y).
  --findings-path FINDINGS_PATH, -f FINDINGS_PATH
                        Path to root of findings store.
  --check-interval CHECK_INTERVAL, -i CHECK_INTERVAL
                        Number of seconds to sleep between checks.
```

Outputs
-------

The executed notebooks are written to the output folder.
The output notebooks use the following naming convention:

{input_notebook}-{identifier}-{execution-date}.ipynb

Example

```bash
ip_explorer-85.214.149.236-2022-08-15T21-23-16-927877+00-00.ipynb
```

Outputs are stored in folders organized by date. The default is

```bash
YEAR
├───MONTH
    └───DAY
```

You can change the granularity of this folder structure with the
`--output-div` switch to `run_notebook`.

Notebook Findings
-----------------

In the notebook logic you will likely want to highlight notebook runs
that contain especially interesting results. You can use nteract
*scrapbook* to indicate this by setting a boolean `Findings` "scrap"
to true. This is shown in the following code.

```python
ti_high_sev = ti_df[ti_df["Severity"] == "high"]
if not ti_high_sev.empty:
    sb.glue("Findings", True)
```

`run_notebook.py` looks for this flag. If it finds one in a finished
notebook it will:

- output a log message that a specific notebook has findings
- create a copy of the output notebook in the findings folder
- create an html copy of the output notebook in the same folder

The names of the notebook and html file will be the same stem of
the output notebook.
