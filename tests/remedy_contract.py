"""The refusal-remedy contract shared by the source-reading guard (repo/test_refusal_remedy.py) and the
behavioural tests that drive each refusal (install/): the "nothing to run here" form of a remedy, and every
unsafe controller-identity cause with the remedy its refusal names."""

NOTHING_TO_RUN = "nothing to run here — "

# Every cause `controller_identity_live` reports as unsafe (its reason template, `{}` for an
# interpolated value) -> a concrete reason and the remedy it gets: a command, or "nothing to run".
_CHECKOUT = "{root}/src/loraham-pi-control"
IDENTITY_CAUSES = {
    "checkout is in detached HEAD":
        ("checkout is in detached HEAD", f"git -C {_CHECKOUT} switch main"),
    "checkout branch {} != {}":
        ("checkout branch 'dev' != 'main'", f"git -C {_CHECKOUT} switch main"),
    "checkout has no origin remote":
        ("checkout has no origin remote", f"git -C {_CHECKOUT} remote add origin {{remote}}"),
    "origin is not the approved canonical remote":
        ("origin is not the approved canonical remote",
         f"git -C {_CHECKOUT} remote set-url origin {{remote}}"),
    "{} is group/other-writable": ("src is group/other-writable", "chmod go-w {root}/src"),
    "controller source_path is not the fixed value":
        ("controller source_path is not the fixed value", None),
    "source path escapes runtime root ({})": ("source path escapes runtime root (x)", None),
    "{} is missing": ("src is missing", None),
    "{} is a symlink (fixed layout required)": ("checkout is a symlink (fixed layout required)", None),
    "{} is not a directory": ("src is not a directory", None),
    "{} not owned by the service user": ("runtime root not owned by the service user", None),
    "checkout realpath escapes the runtime root": ("checkout realpath escapes the runtime root", None),
    "imported package repo != controller checkout":
        ("imported package repo != controller checkout", None),
    "not a git checkout": ("not a git checkout", None),
}
