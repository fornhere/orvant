# Orvant on Windows

[Orvant](../README.md) · [Turkish version](WINDOWS.tr.md)

This guide covers running the Orvant engine on Windows. The project-record runtime already works on Windows with Python 3.10+; this page is about the engine (`orvant` CLI) and its acceptance-oracle isolation.

## What is verified

The following was run and passed on **Windows 11 Home, Python 3.12, Git 2.55, codex-cli 0.158.0** without `PYTHONUTF8` set (default cp1254 console encoding):

- `python -m unittest discover -s engine_tests` — 103 tests, 2 POSIX-only skips, OK.
- `python -m orvant_op --help` and the subcommands `karsila`, `mimar`, `yurut`, `operator`, `surdur` start and produce UTF-8 output.
- Model-free flow: `orvant karsila baslat ...` and `sorular` work.
- Skill project-record demo (`skills/orvant/scripts/project.py init ...`, `context`) works.
- Oracle isolation works with `codex sandbox` in Windows restricted-token **elevated** mode: 22 real-sandbox tests pass, external writes and external TCP are blocked, and the gate is fail-closed.

Other Windows versions, other Python versions, other Codex CLI versions, and the unelevated sandbox mode are **not verified**.

## Prerequisites

- Git for Windows (required for POSIX-style acceptance commands; see below).
- Python 3.11 or newer.
- Codex CLI 0.158.0 installed and signed in, with **elevated** Windows sandbox enabled. Unelevated mode is not accepted by Orvant because it cannot prove network blocking.
- Administrator access at least once, so Codex can install its elevated sandbox.

## Install

Open PowerShell in the repository root.

```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
py -3 -m pip install .
```

The sandbox user (`CodexSandboxOffline`) **cannot** run Python from your user profile, so you must create a separate, readable Python copy before running any oracle-backed command:

```powershell
py -3 -m orvant_op.mimar.yalitim_windows kur
```

This copies about 32 MB of the Python runtime to `%PUBLIC%\orvant-py` (or another path you give with `--hedef`), strips large/unneeded libraries, and hardens the ACL so only Administrators, SYSTEM, you, and Users (read+execute) have access. It does **not** install silently; the command must be run explicitly.

You can also point Orvant at an existing copy with the `ORVANT_KEHANET_PYTHON` environment variable:

```powershell
$env:ORVANT_KEHANET_PYTHON = "C:\Path\To\orvant-py\python.exe"
```

Check that the sandbox can see and run the copy:

```powershell
py -3 -m orvant_op.mimar.yalitim_windows durum
```

If `yalitim` is not `"codex-sandbox"`, the engine will refuse to run the oracle. No isolation means no oracle run.

## Running commands

If the package installed `orvant` onto PATH, use the same commands as on Linux:

```powershell
orvant karsila baslat "C:\OrvantSessions\demo" --hedef "Add a JSON export command"
```

If `orvant` is not on PATH, use the module form:

```powershell
py -3 -m orvant_op karsila baslat "C:\OrvantSessions\demo" --hedef "Add a JSON export command"
```

Set the worker with an environment variable:

```powershell
$env:ORVANT_YURUTUCU = "codex"
```

## How isolation works on Windows

Linux uses `bwrap` for the oracle sandbox. Windows has no `bwrap`, so Orvant uses Codex CLI's `codex sandbox` restricted-token sandbox in elevated mode. Before the oracle runs, Orvant launches a small probe inside the sandbox that must prove:

- It cannot write outside the gate tree (user profile, TEMP, `%SystemRoot%`, and the sandbox Python directory are tested).
- External TCP is blocked by the firewall (`192.0.2.1:9` is used as the probe target).

Only `PermissionError` counts as proof. Any other result is treated as "no proof" and the gate stays closed. The elevated mode is required because only elevated mode installs the firewall rule that blocks outbound TCP; the unelevated mode cannot prove network isolation, so Orvant rejects it.

Process cleanup uses Windows Job Objects: on timeout or cancellation the whole process tree, including grandchildren, is terminated.

## Verified engine behavior

- File locking (`LockFileEx`), atomic write (retrying `os.replace`), junction/symlink rejection, UTF-8 file I/O, and LF line endings (via `.gitattributes eol=lf`) work on Windows.
- Acceptance commands written in POSIX syntax (single quotes, `$`, backticks) are run through Git Bash from Git for Windows. If Git Bash is missing, Orvant falls back to `cmd.exe` and reports a clear error for POSIX-only commands.
- Turkish paths, Turkish input files, and the `ORVANT_GIRDILER` environment variable work with UTF-8.

## Known limits and unverified items

- **DNS leak (Windows-only security boundary):** inside the sandbox, DNS resolution may still reach external servers through the system's DNS client service. The oracle script itself cannot use the network, but a worker program launched by `isci_calistir` could exfiltrate data through DNS. Linux does not have this channel. If you run untrusted workers, use a machine or user account that holds no secrets, or use Linux.
- **Sandbox read scope:** the sandbox can read the whole disk at roughly the same level as Linux `bwrap --ro-bind / /`. Files in your user profile are readable inside the sandbox.
- **Unelevated Codex sandbox** is not verified and is expected to be rejected by Orvant because outbound TCP cannot be proven blocked.
- **Linux and macOS** code paths were preserved during the port but not re-run on those platforms.
- **Long paths:** Windows `MAX_PATH` is 260 characters by default. Each task uses a separate worktree, so paths can grow quickly. Enable `git config --global core.longpaths true` and, if needed, the Windows long-path support registry setting.
- **Symlinks/junctions:** creating symbolic links on Windows requires Developer Mode or administrator rights. Tests that need this are skipped.
- **dir_fd and O_NOFOLLOW** are not available on Windows. Race-window protection for `teshis/gecici_artik` and `yurutme/kat_kapi` is weaker than on POSIX against a concurrent local attacker.
- **Claude Code worker on native Windows** was not verified. Orvant provides its own oracle isolation, but the worker-process find/manage code for Claude Code on Windows is unverified.
- **Python 3.11** is present in CI but was not verified on Windows locally.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `CreateProcessAsUserW failed: 5` | Sandbox user cannot read your profile Python. | Run `py -3 -m orvant_op.mimar.yalitim_windows kur`. |
| `yalitim: yok` in `durum` | Codex sandbox is not elevated, or the firewall rule is missing. | Run Codex CLI setup as administrator; verify elevated mode. |
| POSIX acceptance command fails | Git Bash is not installed. | Install Git for Windows. |
| Path-too-long errors | `MAX_PATH` hit. | `git config --global core.longpaths true`; enable Windows long paths. |

## Beta scope

The same beta limits apply on Windows: start with small Python CLIs, data automation, or narrow repository maintenance. Broad autonomous project completion and reduced user effort have not been established.
