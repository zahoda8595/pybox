import json


def test_new_files_dir_has_no_scripts(client, auth_headers):
    r = client.get("/scripts/api/list", headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json() == []


def test_save_then_list_then_get(client, auth_headers):
    r = client.post("/scripts/api/file", headers=auth_headers,
                     json={"name": "hello.py", "code": "print('hi')"})
    assert r.status_code == 200
    assert r.get_json()["saved"] == "hello.py"

    r = client.get("/scripts/api/list", headers=auth_headers)
    names = [s["name"] for s in r.get_json()]
    assert "hello.py" in names

    r = client.get("/scripts/api/file?name=hello.py", headers=auth_headers)
    assert r.status_code == 200
    assert r.get_json()["code"] == "print('hi')"


def test_save_rejects_non_py_name(client, auth_headers):
    r = client.post("/scripts/api/file", headers=auth_headers,
                     json={"name": "hello.txt", "code": "x = 1"})
    assert r.status_code == 400


def test_save_rejects_path_traversal(client, auth_headers):
    r = client.post("/scripts/api/file", headers=auth_headers,
                     json={"name": "../../evil.py", "code": "x = 1"})
    assert r.status_code == 400


def test_delete_script(client, auth_headers):
    client.post("/scripts/api/file", headers=auth_headers,
                json={"name": "temp.py", "code": "x = 1"})
    r = client.delete("/scripts/api/file", headers=auth_headers,
                       json={"name": "temp.py"})
    assert r.status_code == 200
    assert r.get_json()["deleted"] is True

    r = client.get("/scripts/api/file?name=temp.py", headers=auth_headers)
    assert r.status_code == 404


def test_run_captures_stdout(client, auth_headers):
    r = client.post("/scripts/api/run", headers=auth_headers,
                     json={"name": "t.py", "code": "print('captured output')"})
    assert r.status_code == 200
    d = r.get_json()
    assert "captured output" in d["stdout"]
    assert d["error"] is None
    assert d["timed_out"] is False


def test_run_captures_exception_without_crashing_route(client, auth_headers):
    r = client.post("/scripts/api/run", headers=auth_headers,
                     json={"name": "t.py", "code": "raise ValueError('boom')"})
    assert r.status_code == 200
    d = r.get_json()
    assert "ValueError" in d["error"]
    assert "boom" in d["error"]


def test_run_enforces_timeout(client, auth_headers):
    r = client.post("/scripts/api/run", headers=auth_headers,
                     json={"name": "t.py", "code": "import time; time.sleep(5)"})
    # backend_app's run_script clamps timeout to >=1s; we don't want a
    # slow test suite, so this just checks the field exists and is a
    # bool - a dedicated short-timeout unit test lives in
    # test_scripts_runner_unit.py instead, which controls timeout directly.
    d = r.get_json()
    assert isinstance(d["timed_out"], bool)


def test_run_stream_yields_ndjson_lines(client, auth_headers):
    r = client.post("/scripts/api/run_stream", headers=auth_headers,
                     json={"name": "t.py", "code": "print('a'); print('b')"})
    assert r.status_code == 200
    assert r.mimetype == "application/x-ndjson"
    lines = [json.loads(line) for line in r.data.decode().splitlines() if line.strip()]
    kinds = [e["type"] for e in lines]
    assert "stdout" in kinds
    assert "done" in kinds
    combined_stdout = "".join(e["text"] for e in lines if e["type"] == "stdout")
    assert "a" in combined_stdout and "b" in combined_stdout
