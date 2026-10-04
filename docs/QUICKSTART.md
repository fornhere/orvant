# Orvant quickstart

[Orvant](../README.md) · [Turkish quickstart](HIZLI-BASLANGIC.md)

## 1. Try the skill in 60 seconds

This path needs Python 3.10+; it does not need a coding agent CLI, an API key, or an installed
Orvant package.

```sh
git clone https://github.com/fornhere/orvant.git
cd orvant
demo="$(mktemp -d)/demo"
python3 skills/orvant/scripts/project.py init "$demo" --spec examples/demo-spec.json
python3 "$demo/.project/scripts/project.py" check "$demo"
python3 "$demo/.project/scripts/project.py" context "$demo"
python3 "$demo/.project/scripts/project.py" ontology "$demo"
```

`init` copies the project runtime into `.project/` and **writes `AGENTS.md` in
the target directory**. Use a disposable directory, as above, when evaluating
it. `check` validates the record; `context` shows ready and blocked work; and
`ontology` renders the live object map. The committed outputs for this exact
spec are in [`sample-output/`](sample-output/KOMUTLAR.md).

## 2. Install the engine

The engine requires Linux, Git, Python 3.11+, and installed, signed-in **Codex CLI or Claude Code (one is enough)**. Both are supported equally; neither is the default or experimental. It was developed with `codex-cli 0.155.1`; other versions are unverified, and no verified Claude Code version is claimed. Engine behavior on macOS or Windows is unverified.

Worker selection: `ORVANT_YURUTUCU=codex|claude` takes precedence over `orvant.toml` with `[yurutucu] tur = "codex"` or `tur = "claude"`. Without either setting, exactly one installed executable (on PATH or at its configured path) is detected automatically. If both are installed, explicit selection is required; otherwise Orvant reports `iki yürütücü bulundu` (two executors found).

Oracle OS isolation on Linux requires bubblewrap (`bwrap`) or Codex sandbox (`codex sandbox`), independently of the selected worker. In `orvant.toml`, `[kehanet] yalitim = "auto"` tries verified `bwrap` first, then Codex sandbox; `yalitim = "bwrap"` or `yalitim = "codex"` forces that backend. If no usable isolation backend is available, Orvant does not run the oracle: it fails closed and never runs it without isolation.

```sh
git clone https://github.com/fornhere/orvant.git
cd orvant
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
orvant --help
```

The wheel installs the engine, not the skill files. Do not run the model-calling
commands below until the selected CLI is signed in and the repository is ready.

## 3. Start an engine session

Replace every angle-bracket placeholder. Keep `<engine-session>` and
`<product-repo>` outside the Orvant checkout and outside one another. The first
plan requires `<product-repo>` to be a clean Git repository on `main`.

| Command | Calls a model? | User decision? |
| --- | --- | --- |
| `orvant karsila baslat "<engine-session>" --hedef "<goal>"` | No | Supplies the user's goal |
| `orvant karsila ilerle "<engine-session>"` | Yes, when it performs an automatic intake step | No; never invent an answer |
| `orvant karsila durum "<engine-session>"` | No | No; read-only status |
| `orvant karsila sorular "<engine-session>"` | No | No; read-only questions |
| `orvant karsila cevapla "<engine-session>" "<question-id>" "<answer>"` | No | **Yes; record only the real user's answer** |
| `orvant karsila sozlesme "<engine-session>"` | No | No; inspect the proposed contract |
| `orvant karsila onayla "<engine-session>" "<revision>"` | No | **Yes; only the user approves the displayed revision** |
| `orvant mimar plan "<engine-session>" --depo "<product-repo>"` | Yes | No; creates a plan from the approved contract |
| `orvant mimar durum "<engine-session>"` | No | No; read-only plan status |
| `orvant mimar yetki "<engine-session>"` | No | No; read-only permission requests |
| `orvant mimar izin "<engine-session>" "<request-id>" "<answer>" --karar verildi` | No | **Yes; only the user grants or denies permission** |

Use `--karar reddedildi` instead of `verildi` when the user denies a permission.
Contract approval does not grant every requested permission. Repeat `ilerle`
only after reading its result, carry each question to the user, and use the
actual IDs and revision printed by the session.

## 4. Continue an existing plan

Once a plan exists, continue the same session; do not run `karsila baslat` or
`mimar plan` again.

| Command | Calls a model? | User decision? |
| --- | --- | --- |
| `orvant surdur "<engine-session>" --kuru` | No | No; previews actions and accepts no work |
| `orvant surdur "<engine-session>" --en-fazla-tur 5 --tur-basina-kosu 3` | Yes, when authorized work can progress | No; it may modify the product repository |
| `orvant operator sorular "<engine-session>"` | No | No; read-only queued questions |
| `orvant operator cevapla "<engine-session>" "<question-id>" "<answer>"` | No | **Yes; record only the real user's answer** |
| `orvant yurut durum "<engine-session>"` | No | No; read-only execution status |

Read the continuation report, task gate results, receipts, and open questions
together. A zero exit status alone does not mean that the project is complete.

## 5. Know when it has not succeeded

Do **not** treat any of these stop reasons as success:
`kullanici_bekleniyor` (waiting for the user), `kota` (quota), `zaman_asimi`
(timeout), `ilerleme_yok` (no progress), or `orvant_duzeltmesi_bekleniyor`
(waiting for an Orvant fix).

A worker's `complete` claim is not acceptance. One accepted task is not a
completed project, and a zero process exit status is not completion. Check the
independent gate, current task states, pending review or quarantine, receipts,
and unresolved questions. Continue the same session only when a real answer,
permission, or other authorized progress is available.

## 6. Next steps

For the complete command reference, see [Usage (Turkish)](KULLANIM.md). For
acceptance behavior, session records, limitations, and stop reasons, see
[Engine (Turkish)](MOTOR.md). The experimental `orvant proje` command is also
present, but it is outside this quickstart flow.
