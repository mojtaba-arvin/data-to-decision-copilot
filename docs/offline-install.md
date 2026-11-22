# Offline installation and local Ollama operation

This guide prepares a deployment bundle on a connected machine and installs it
on a machine that has no internet access. Prepare the bundle for the same
operating system, CPU architecture, and Python minor version as the offline host.

## 1. Build the application and runtime wheelhouse online

Use Python 3.13 and a current `uv` installation:

```bash
uv export --locked --format requirements-txt --no-dev --no-emit-project -o requirements-runtime.txt
python -m pip download --only-binary=:all: -r requirements-runtime.txt -d wheelhouse/
uv build
cp dist/*.whl wheelhouse/
```

The bundle should contain:

- `wheelhouse/` with the application wheel and all runtime dependency wheels.
- The exact `requirements-runtime.txt` used to prepare the wheelhouse.
- The application source archive if source-level inspection is required.
- This project's documentation and the intended catalog/data files.

Do not use a wheelhouse prepared for a different OS, architecture, or Python
minor version. Keep the build output and dependency manifest with the bundle.

## 2. Install without network access

On the offline host, create and activate a Python 3.13 virtual environment, then
install exclusively from the local wheelhouse:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install --no-index --find-links ./wheelhouse data_to_decision_copilot-0.1.0-py3-none-any.whl
d2d --help
```

The `--no-index` flag prevents pip from contacting a package index. If a
dependency wheel is missing, installation fails rather than silently reaching
the network. Add the missing wheel to the prepared bundle and retry.

## 3. Prepare Ollama and the model before disconnecting

On a connected machine with the same Ollama version and compatible hardware:

```bash
ollama pull qwen3:8b
ollama list
ollama show qwen3:8b
```

Copy the Ollama model store to the offline host using the Ollama-supported model
storage location for that installation. Set `OLLAMA_MODELS` to the transferred
model directory before starting the daemon. Also transfer the Ollama installer
or platform package itself; installing the Python application does not install
Ollama.

On the offline host, verify that the daemon is local and the model is present:

```bash
ollama list
curl --fail http://127.0.0.1:11434/api/tags
```

The provider accepts loopback HTTP only. It does not download models, contact
hosted inference services, or retry failed generations.

## 4. Run the application

The default deterministic planner requires no model service:

```bash
d2d schema --catalog ./analytics.duckdb
d2d query --catalog ./analytics.duckdb --question "count rows in orders"
```

Opt into the local Ollama provider explicitly:

```bash
d2d query --catalog ./analytics.duckdb --provider ollama --model qwen3:8b --question "count rows in orders"
```

Keep the REST API bound to loopback. The current product interface does not
provide remote authentication or authorization.

## 5. Verify offline behavior

- Disconnect the host from external networks.
- Confirm the model is already present with `ollama list`.
- Run the deterministic query first, then the Ollama query.
- Inspect local logs for failures; do not enable retries that could hide repeated
  inference or execution failures.
- Record the application wheel hash, dependency manifest, Ollama version, model
  tag, and hardware alongside any benchmark results.

A model tag is descriptive metadata, not a cryptographic model attestation.
