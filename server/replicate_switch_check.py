"""The Replicate switch and tick gate. No network."""
import json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import host_paths


def main() -> int:
    bad = 0
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "replicate-settings.json")
    os.environ["REPLICATE_SETTINGS_PATH"] = path
    for key in ("VECTOR_ESRGAN", "VECTOR_SKIP_ESRGAN", "FLYERZ_REPLICATE_TICK"):
        os.environ.pop(key, None)

    def check(name, got, want):
        nonlocal bad
        ok = got == want
        bad += 0 if ok else 1
        print(("PASS " if ok else "FAIL ") + name, got)

    check("missing file means On", host_paths.replicate_switch_on(), True)
    check("old behaviour: no flag, no tick -> off", host_paths.esrgan_enabled(), False)
    os.environ["VECTOR_ESRGAN"] = "1"
    check("old behaviour: VECTOR_ESRGAN=1 no tick -> on", host_paths.esrgan_enabled(), True)
    os.environ["FLYERZ_REPLICATE_TICK"] = "0"
    check("unticked job with VECTOR_ESRGAN=1 -> never", host_paths.esrgan_enabled(), False)
    os.environ["FLYERZ_REPLICATE_TICK"] = "1"
    check("ticked, switch On -> on", host_paths.esrgan_enabled(), True)
    os.environ.pop("VECTOR_ESRGAN")
    check("ticked, VECTOR_ESRGAN unset, switch On -> on", host_paths.esrgan_enabled(), True)
    json.dump({"enabled": False}, open(path, "w"))
    check("switch Off read live", host_paths.replicate_switch_on(), False)
    check("ticked, switch Off -> never", host_paths.esrgan_enabled(), False)
    os.environ["VECTOR_ESRGAN"] = "1"
    os.environ.pop("FLYERZ_REPLICATE_TICK")
    check("VECTOR_ESRGAN=1, switch Off -> never", host_paths.esrgan_enabled(), False)
    import ai_enhancements

    os.environ["REPLICATE_API_TOKEN"] = "r8_fake"
    out, err = ai_enhancements._call_replicate("x", "o", "m", {})
    check("_call_replicate refuses when Off", (out, err), (None, "Replicate is switched off in settings"))
    open(path, "w").write("not json")
    check("broken file means On", host_paths.replicate_switch_on(), True)
    print("FAILED" if bad else "ALL PASS")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
