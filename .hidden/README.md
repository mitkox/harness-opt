# Evaluator-owned sealed material. Agent workers, optimizer jobs, and packaging
# must never read, mount, copy, or depend on this directory. It is excluded
# from workspace snapshots by construction (the runner only copies the case
# repo/ subdirectory). Tests in tests/test_verification.py assert the seal.
