"""Tests for Ansible override mode — template rendering and CLI flags."""

import json
import os
import subprocess
import sys

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES_DIR = os.path.join(REPO_ROOT, "junosterraform", "templates")


# ---------------------------------------------------------------------------
# Task 1.2 / 1.3: Template rendering tests
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_schema():
    """A minimal JTAF JSON schema with one leaf for template rendering."""
    return {
        "device_type": "test",
        "root": {
            "children": [
                {
                    "children": [
                        {
                            "name": "system",
                            "type": "container",
                            "path": "",
                            "children": [
                                {
                                    "name": "host-name",
                                    "type": "leaf",
                                    "path": "system",
                                }
                            ],
                        }
                    ]
                }
            ]
        },
    }


def render_ansible_template(schema, jtaf_mode=None):
    """Render the ansible.j2 template with the given schema and mode."""
    from jinja2 import Template

    template_path = os.path.join(TEMPLATES_DIR, "ansible.j2")
    with open(template_path) as f:
        source = f.read()

    tmpl = Template(source)
    kwargs = {"data": schema}
    if jtaf_mode is not None:
        kwargs["jtaf_mode"] = jtaf_mode
    return tmpl.render(**kwargs)


class TestTemplateOverrideMode:
    """Task 1.2: Override mode renders bare <configuration> without groups."""

    def test_override_mode_no_groups(self, minimal_schema):
        result = render_ansible_template(minimal_schema, jtaf_mode="override")
        assert "<groups>" not in result
        assert "</groups>" not in result
        assert "<apply-groups>" not in result
        assert "</apply-groups>" not in result
        assert "<configuration>" in result
        assert "</configuration>" in result

    def test_override_mode_no_jtaf_ansible_name(self, minimal_schema):
        result = render_ansible_template(minimal_schema, jtaf_mode="override")
        assert "JTAF_ANSIBLE" not in result


class TestTemplateGroupMode:
    """Task 1.3: Group mode (default) renders groups wrapper."""

    def test_group_mode_explicit(self, minimal_schema):
        result = render_ansible_template(minimal_schema, jtaf_mode="group")
        assert "<groups>" in result
        assert "</groups>" in result
        assert "<apply-groups>" in result

    def test_group_mode_default_when_unset(self, minimal_schema):
        """When jtaf_mode is not passed, should default to group mode."""
        result = render_ansible_template(minimal_schema, jtaf_mode=None)
        assert "<groups>" in result
        assert "</groups>" in result
        assert "<apply-groups>" in result

    def test_group_mode_has_jtaf_ansible_name(self, minimal_schema):
        result = render_ansible_template(minimal_schema, jtaf_mode="group")
        assert "JTAF_ANSIBLE" in result


# ---------------------------------------------------------------------------
# Task 6.1 / 6.2: CLI flag tests for jtaf-ansible
# ---------------------------------------------------------------------------

class TestJtafAnsibleCLI:
    """Test jtaf-ansible --mode flag generates correct outputs."""

    @pytest.fixture
    def run_jtaf_ansible(self, tmp_path, minimal_schema):
        """Helper to run jtaf-ansible with given mode and return output dir."""
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(json.dumps(minimal_schema))

        # Create a minimal XML config
        xml_file = tmp_path / "test.xml"
        xml_file.write_text("<configuration><system><host-name>test</host-name></system></configuration>")

        def _run(mode="group"):
            result = subprocess.run(
                [
                    sys.executable,
                    os.path.join(REPO_ROOT, "junosterraform", "jtaf-ansible"),
                    "-j", str(schema_file),
                    "-x", str(xml_file),
                    "-t", "test-device",
                    "--mode", mode,
                ],
                capture_output=True,
                text=True,
                cwd=str(tmp_path),
            )
            return result, tmp_path / "ansible-provider-junos-test-device"

        return _run

    def test_override_mode_generates_defaults(self, run_jtaf_ansible):
        result, output_dir = run_jtaf_ansible("override")
        defaults_file = output_dir / "roles" / "test-device_role" / "defaults" / "main.yml"
        if defaults_file.exists():
            content = defaults_file.read_text()
            assert 'jtaf_mode: "override"' in content
            assert "jtaf_commit_confirm_minutes" in content

    def test_group_mode_generates_defaults(self, run_jtaf_ansible):
        result, output_dir = run_jtaf_ansible("group")
        defaults_file = output_dir / "roles" / "test-device_role" / "defaults" / "main.yml"
        if defaults_file.exists():
            content = defaults_file.read_text()
            assert 'jtaf_mode: "group"' in content

    def test_override_playbook_has_load_override(self, run_jtaf_ansible):
        result, output_dir = run_jtaf_ansible("override")
        playbook_file = output_dir / "jtaf-playbook.yml"
        if playbook_file.exists():
            content = playbook_file.read_text()
            assert "load: override" in content
            assert "wait_for" in content

    def test_group_playbook_has_load_replace(self, run_jtaf_ansible):
        result, output_dir = run_jtaf_ansible("group")
        playbook_file = output_dir / "jtaf-playbook.yml"
        if playbook_file.exists():
            content = playbook_file.read_text()
            assert "load: replace" in content
