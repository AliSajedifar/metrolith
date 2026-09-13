"""Exercise the scoped CRLF policy with real Git diffs and exact file bytes."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "validation/conformance/data/manifest.json"


class GitWhitespacePolicyTests(unittest.TestCase):
    def test_scoped_crlf_rule_and_negative_controls(self):
        policy = (ROOT / ".gitattributes").read_bytes()
        prefix = (MANIFEST + " whitespace=").encode()
        self.assertEqual(sum(line.startswith(prefix) for line in policy.splitlines()), 1)
        prior = b"".join(line for line in policy.splitlines(keepends=True)
                         if not line.startswith(prefix))
        cases = [
            ("before-target-crlf", prior, MANIFEST, b'{}\r\n', "trailing whitespace"),
            ("target-crlf", policy, MANIFEST, b'{"notes": "valid"}\r\n', None),
            ("target-lf", policy, MANIFEST, b'{}\n', None),
            ("space-crlf", policy, MANIFEST, b'{} \r\n', "trailing whitespace"),
            ("tab-crlf", policy, MANIFEST, b'{}\t\r\n', "trailing whitespace"),
            ("indent", policy, MANIFEST, b' \t"notes": "invalid"\r\n', "space before tab"),
            ("blank-eof", policy, MANIFEST, b'{}\r\n\r\n', "blank line at EOF"),
            ("conflict", policy, MANIFEST, b'<<<<<<< ours\r\n=======\r\n>>>>>>> theirs\r\n', "conflict marker"),
            ("other-lf-space", policy, "other.json", b'{} \n', "trailing whitespace"),
            ("other-before", prior, "other.json", b'{}\r\n', "trailing whitespace"),
            ("other-after", policy, "other.json", b'{}\r\n', "trailing whitespace"),
            ("nearby-before", prior, "validation/conformance/data/other.json", b'{}\r\n', "trailing whitespace"),
            ("nearby-after", policy, "validation/conformance/data/other.json", b'{}\r\n', "trailing whitespace"),
        ]
        # Isolate only the synthetic fixture repositories from ambient Git
        # identity/configuration. No real product/user configuration is changed.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        for label, attributes, relative, payload, diagnostic in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory(prefix="git-whitespace-") as directory:
                fixture = Path(directory)

                def git(*arguments, check=True):
                    return subprocess.run(
                        ["git", *arguments], cwd=fixture, env=environment,
                        check=check, capture_output=True,
                    )

                def commit():
                    git("-c", "user.name=Synthetic Whitespace Test",
                        "-c", "user.email=whitespace-test@example.invalid",
                        "commit", "--quiet", "-m", "Synthetic fixture")

                def check_diff(stage, *arguments):
                    checked = git("diff", "--check", *arguments, check=False)
                    output = (checked.stdout + checked.stderr).decode("utf-8", errors="replace")
                    print(f"{label}/{stage}: exit={checked.returncode} {output.strip()}", flush=True)
                    if diagnostic is None:
                        self.assertEqual(checked.returncode, 0, output)
                    else:
                        self.assertNotEqual(checked.returncode, 0, output)
                        self.assertIn(diagnostic, output)

                git("init", "--quiet", "--template=")
                (fixture / ".gitattributes").write_bytes(attributes)
                target = fixture / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b'{"baseline": true}\n')
                git("add", "--", ".gitattributes", relative)
                commit()
                target.write_bytes(payload)
                check_diff("working")
                git("add", "--", relative)
                self.assertEqual(git("show", f":{relative}").stdout, payload)
                check_diff("staged", "--cached")
                commit()
                self.assertEqual(git("show", f"HEAD:{relative}").stdout, payload)
                check_diff("committed", "HEAD^", "HEAD")
                target.unlink()
                git("checkout", "HEAD", "--", relative)
                self.assertEqual(target.read_bytes(), payload)
                self.assertEqual(git("status", "--porcelain").stdout, b"")


if __name__ == "__main__":
    unittest.main()
