"""Pack: reading the files the owner leaves for the agent (CSV exports, counts, lists)."""
from agent.tools import READ, Pack, Tool


def _list_files(ws, a):
    return sorted(p.name for p in ws.files_dir.iterdir() if p.is_file())


def _read_file(ws, a):
    # helps keep the existence check focused on a filename inside files_dir
    if not (ws.files_dir / a["name"].split("/")[-1]).exists():
        raise FileNotFoundError(f"no file named {a['name']}")
    return ws.read_csv(a["name"])  # returns its rows as a list of dictionaries.


PACK = Pack(
    name="files",
    tools=[
        Tool("list_files", "List the files the owner left for you.", {}, READ, _list_files),
        Tool("read_file", "Read a CSV file from the files folder. Returns a list of rows.",
             {"name": (str, "file name, e.g. payments.csv")}, READ, _read_file),
    ],
    guidance="The owner leaves exports and lists as CSV files. Check list_files if you're not sure what exists.",
)
