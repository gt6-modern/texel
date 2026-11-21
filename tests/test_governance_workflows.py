import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class GovernanceWorkflowTest(unittest.TestCase):
    def test_external_actions_are_full_commit_pins(self):
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            for action in re.findall(r'^\s*-?\s*uses:\s*(\S+)', path.read_text(encoding='utf-8'), re.M):
                if not action.startswith('./'):
                    self.assertRegex(action, r'^[\w.-]+/[\w./-]+@[0-9a-f]{40}$', str(path))

    def test_codeowners_covers_whole_repository(self):
        self.assertEqual('* @huangsijun17 @mydanyi',
                         (ROOT / '.github/CODEOWNERS').read_text(encoding='utf-8').strip())

    def test_required_summary_covers_every_job_and_checks_success(self):
        text = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
        jobs = set(re.findall(r'^  ([\w-]+):', text.split('jobs:', 1)[1], re.M))
        summary = text.split('\n  required:', 1)[1]
        needs = set(x.strip() for x in re.search(r'needs: \[(.*?)\]', summary)[1].split(','))
        self.assertEqual(jobs - {'required'}, needs)
        self.assertIn('name: CI required', summary)
        self.assertIn('if: always()', summary)
        self.assertIn('toJSON(needs)', summary)
        self.assertIn('success', summary)

    def test_workflow_has_one_release_decision_entrypoint(self):
        text = (ROOT / '.github/workflows/release.yml').read_text(encoding='utf-8')
        self.assertIn('python tools/release_state.py --publish', text)
        self.assertNotIn('softprops/', text)
        self.assertNotIn('gh release ', text)
        self.assertIn('cancel-in-progress: false', text)
        self.assertIn('upload-artifact: false', text)  # SBOM stays local until checksums/attestation.
        self.assertIn('upload-release-assets: false', text)

    def test_gradle_distribution_is_verified(self):
        # Update distributionUrl, its SHA-256, and this assertion together on upgrades.
        text = (ROOT / 'compiler/java/gradle/wrapper/gradle-wrapper.properties').read_text(encoding='utf-8')
        self.assertIn('gradle-9.2.1-bin.zip', text)
        self.assertIn('distributionSha256Sum=72f44c9f8ebcb1af43838f45ee5c4aa9c5444898b3468ab3f4af7b6076c5bc3f', text)


if __name__ == '__main__':
    unittest.main()
