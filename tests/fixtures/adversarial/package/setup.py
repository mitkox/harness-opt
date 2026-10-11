import pathlib

pathlib.Path("MALICIOUS-LIFECYCLE-RAN").write_text("executed")
raise SystemExit("malicious lifecycle script")
