"""Regression tests for TLS trust inside the dev sandbox."""

from pathlib import Path


STAGE2_RUN = Path(__file__).parents[1] / "scripts" / "sandbox" / "stage2-run.sh"


def test_node_trusts_the_ca_used_by_the_sandbox_mitm_proxy():
    script = STAGE2_RUN.read_text(encoding="utf-8")
    node_ca_lines = [
        line.strip().removesuffix("\\").rstrip()
        for line in script.splitlines()
        if "--setenv NODE_EXTRA_CA_CERTS" in line
    ]

    assert node_ca_lines == ["--setenv NODE_EXTRA_CA_CERTS /work/certs/ca.pem"]
