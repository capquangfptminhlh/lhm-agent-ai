"""Install the verified upstream Agent-Reach release into the Windows build venv.

Only for the operator/build pipeline; never callable by AI Computer tool.
Avoid the unrelated PyPI distribution named agent-reach.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

URL="https://github.com/Panniantong/Agent-Reach/releases/download/v1.6.0/agent_reach-1.6.0-py3-none-any.whl"
SHA256="79455eafd94302561aa5f52b0722a0fc359279c3170a27c6990486027fd56830"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="lhm-reach-release-") as tmp:
        wheel=Path(tmp)/"agent_reach-1.6.0-py3-none-any.whl"
        request=urllib.request.Request(URL,headers={"User-Agent":"LHM-Windows-Release-Build"})
        digest=hashlib.sha256()
        with urllib.request.urlopen(request, timeout=60) as source, wheel.open("wb") as dest:
            while chunk:=source.read(65536):
                digest.update(chunk)
                dest.write(chunk)
        if digest.hexdigest()!=SHA256:
            raise SystemExit("SECURITY: Agent-Reach release wheel SHA256 does not match pinned GitHub digest.")
        print("GitHub Agent-Reach v1.6.0 wheel digest verified",flush=True)
        subprocess.run([sys.executable,"-m","pip","install","--no-cache-dir",
                        str(wheel)],check=True,timeout=240)
    from agent_reach import __version__
    if __version__!="1.6.0":
        raise SystemExit("Unexpected Agent-Reach version "+__version__)
    print("Windows Agent-Reach installed from verified upstream GitHub release",flush=True)


if __name__=="__main__":
    main()
