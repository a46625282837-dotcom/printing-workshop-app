"""اختبار ذاتي شامل للطبقة الصامتة — يعمل في ثوانٍ، بلا واجهة.

تشغيل::

    python -m core.selftest

يرجع 0 عند النجاح، 1 عند وجود مشاكل/تجاوزات. يُشغَّل بعد أي تعديل مستقبلي
على الأقسام للتأكد أن تعديلاتك لم تكسر طبقة المسارات أو الموارد.
"""

import os
import sys


def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from core import runtime
    from core import selfcheck

    report_text, problems = selfcheck.build_report()

    tl = report_text.splitlines() or []
    print("\n".join(tl))
    print()
    failures = 0
    warns = 0

    def mark(ok, label, detail=""):
        nonlocal failures, warns
        if ok:
            print("PASS  | %s%s" % (label, (" | " + detail) if detail else ""))
        else:
            failures += 1
            print("FAIL  | %s%s" % (label, (" | " + detail) if detail else ""))

    install_ascii = runtime.path_is_ascii(runtime.exe_dir())
    if not runtime.is_frozen() or install_ascii:
        print("PASS  | path resilience | works in this install path")
    else:
        warns += 1
        print("WARN  | path resilience | non-ASCII install path (supported, diagnostics only)")
    ddir = runtime.data_dir()
    mark(os.path.isdir(ddir), "data dir", ddir)

    res = selfcheck.resource_manifest()
    mark(all(e for _, e, _ in res), "critical bundle files",
         "%d/%d present" % (sum(1 for _, e, _ in res if e), len(res)))

    bypass = selfcheck.scan_source_for_bypass()
    mark(not bypass, "runtime-layer compliance", "clean" if not bypass else "; ".join(bypass))
    if bypass:
        print()
        print("WARNING: التعديلات المستقبلية تجاوزت طبقة الموارد:")
        for b in bypass:
            print("   - %s" % b)

    print()
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print("  - %s" % p)
        print()
    print("SUMMARY: %d fail(s), %d problem(s) -> %s"
          % (failures, len(problems), "FAIL" if (failures or problems) else "PASS"))
    return 0 if not (failures or problems) else 1


if __name__ == "__main__":
    sys.exit(run())