# JTAF Ansible Override Mode — Demo Walkthrough

## Overview

This document walks through a complete end-to-end demo of JTAF's new **Ansible Override Mode** feature. It shows how to generate an Ansible role that uses `load override` + `commit confirmed` to push full device configuration safely to Junos devices.

### What is Override Mode?

JTAF now supports two operating modes for Ansible:

| Mode | How it works | Use case |
|------|-------------|----------|
| **Group** (default) | Wraps config in `<groups>JTAF_ANSIBLE</groups>`, uses `load replace` | You manage a subset of the device config |
| **Override** (new) | Renders bare `<configuration>`, uses `load override` + `commit confirmed` | You own the entire device config |

**Key insight:** In override mode, Junos candidate config IS the diff engine — no patch engine needed. You push the full config, Junos calculates what changed internally, and `commit confirmed` provides auto-rollback safety if something goes wrong.

---

## Prerequisites

```bash
# On the NITA server (or your Ansible control node)
cd ~/junos-terraform

# Switch to the feature branch
git checkout ansible-override-mode
git pull origin ansible-override-mode

# Install JTAF tools
pip install -e .

# Install Ansible dependencies
pip install ansible junos-eznc jxmlease ncclient
ansible-galaxy collection install juniper.device

# Set credentials
export NETCONF_USERNAME='jcluser'
export NETCONF_PASSWORD='Juniper!1'
```

---

## Phase 1: Generate Ansible Role (Override Mode)

### What we're testing
The `--mode override` flag on `jtaf-yang2ansible` generates a role that:
- Renders bare `<configuration>` XML (no `<groups>` wrapper)
- Includes `defaults/main.yml` with `jtaf_mode: "override"` and `jtaf_commit_confirm_minutes: 2`
- Generates a playbook with `load override` + `commit confirmed` tasks

### Commands

```bash
cd ~/junos-terraform/examples/ansible

# Clean previous output
rm -rf ansible-provider-junos-vqfx-override

# Generate role with override mode
jtaf-yang2ansible \
  -p ../yang/18.2/18.2R3/common ../yang/18.2/18.2R3/junos-qfx/conf/*.yang \
  -x ../evpn-vxlan-dc/dc1/dc1-*leaf* ../evpn-vxlan-dc/dc1/dc1-*spine* \
     ../evpn-vxlan-dc/dc1/dc1-*borderleaf* ../evpn-vxlan-dc/dc2/dc2-*spine* \
  -t vqfx-override \
  --mode override
```

### Verification

```bash
# 1. Check that override mode is set in defaults
cat ansible-provider-junos-vqfx-override/roles/vqfx-override_role/defaults/main.yml
```

Expected output:
```yaml
---
jtaf_mode: "override"
jtaf_commit_confirm_minutes: 2
jtaf_group_name: "JTAF_ANSIBLE"
```

```bash
# 2. Verify NO <groups> wrapper in template (should return 0)
grep -c "<groups>" ansible-provider-junos-vqfx-override/roles/vqfx-override_role/templates/template.j2
```

Expected: `0`

```bash
# 3. Verify playbook has load override tasks
grep "load: override" ansible-provider-junos-vqfx-override/jtaf-playbook.yml
grep "wait_for" ansible-provider-junos-vqfx-override/jtaf-playbook.yml
grep "confirmed" ansible-provider-junos-vqfx-override/jtaf-playbook.yml
```

### What to explain
- "The `--mode override` flag tells JTAF to generate a role without the `<groups>` wrapper"
- "This means the rendered XML IS the entire device configuration — what you provide is what goes on the box"
- "The generated playbook automatically includes commit confirmed safety — if the device becomes unreachable, Junos rolls back"

---

## Phase 2: Generate host_vars / group_vars

### What we're testing
`jtaf-xml2yaml` extracts variables from XML configs into a hierarchical YAML structure that the role uses at render time.

### Commands

```bash
rm -rf ansible_override_files

jtaf-xml2yaml \
  -x ../evpn-vxlan-dc/dc1/dc1-*leaf* ../evpn-vxlan-dc/dc1/dc1-*spine* \
     ../evpn-vxlan-dc/dc1/dc1-*borderleaf* ../evpn-vxlan-dc/dc2/dc2-*spine* \
  -j ansible-provider-junos-vqfx-override/trimmed_schema.json \
  -d ansible_override_files \
  --grouping-hosts-file switches_grouping_hosts
```

### Verification

```bash
# Per-host variable files
ls ansible_override_files/host_vars/

# Shared variables (common to all devices)
ls ansible_override_files/group_vars/

# Check leaf-lists are properly extracted as lists
grep -A3 "protocol" ansible_override_files/host_vars/dc1-spine1.yaml | head -10
```

Expected: `protocol` shows as a YAML list `['direct', 'bgp']`, not a single value.

### What to explain
- "jtaf-xml2yaml extracts configuration from XML into YAML variables"
- "Shared config goes into `group_vars/all.yaml`, device-specific config goes into `host_vars/<device>.yaml`"
- "This hierarchical structure means you edit YAML to make changes — no need to touch XML directly"

---

## Phase 3: Setup ansible.cfg and Inventory

### What we're testing
Proper Ansible configuration so it can find the role, filter plugins, and connect to devices.

### Commands

```bash
# Create ansible.cfg
cat > ansible.cfg << 'EOF'
[defaults]
roles_path = ansible-provider-junos-vqfx-override/roles
filter_plugins = ansible-provider-junos-vqfx-override/filter_plugins
host_key_checking = False
interpreter_python = auto_silent
EOF

# Create test inventory (single device)
cat > test-inventory.ini << 'EOF'
[dc1-spine]
dc1-spine1 ansible_host=100.123.24.3 ansible_port=830
EOF
```

### Create the dry-run playbook

```bash
cat > test-override-dryrun.yml << 'EOF'
---
- name: Render config (override mode)
  hosts: all
  connection: local
  gather_facts: false
  vars:
    tmp_dir: ansible-provider-junos-vqfx-override/configs
    jtaf_vars_root: ansible_override_files
    jtaf_mode: "override"
  roles:
    - role: vqfx-override_role
      delegate_to: localhost

- name: Preview override diff (NO commit)
  hosts: all
  connection: local
  gather_facts: false
  vars:
    netconf_user: "{{ lookup('env', 'NETCONF_USERNAME') }}"
    netconf_pass: "{{ lookup('env', 'NETCONF_PASSWORD') }}"
    tmp_dir: ansible-provider-junos-vqfx-override/configs
  tasks:
    - name: Load override (candidate only, NO commit)
      juniper.device.config:
        host: "{{ ansible_host }}"
        port: "{{ ansible_port | default(830) }}"
        user: "{{ netconf_user }}"
        passwd: "{{ netconf_pass }}"
        load: override
        src: "{{ tmp_dir }}/{{ inventory_hostname }}.xml"
        check: false
        commit: false
        diff: true
      register: preview

    - name: Show what would change
      debug:
        var: preview.diff_lines
      when: preview.diff_lines | default([]) | length > 0

    - name: No changes needed
      debug:
        msg: "No diff — device config matches rendered template exactly."
      when: preview.diff_lines | default([]) | length == 0
EOF
```

### Create the apply playbook

```bash
cat > test-override-apply.yml << 'EOF'
---
- name: Render config (override mode)
  hosts: all
  connection: local
  gather_facts: false
  vars:
    tmp_dir: ansible-provider-junos-vqfx-override/configs
    jtaf_vars_root: ansible_override_files
    jtaf_mode: "override"
  roles:
    - role: vqfx-override_role
      delegate_to: localhost

- name: Apply override with commit confirmed
  hosts: all
  connection: local
  gather_facts: false
  vars:
    netconf_user: "{{ lookup('env', 'NETCONF_USERNAME') }}"
    netconf_pass: "{{ lookup('env', 'NETCONF_PASSWORD') }}"
    tmp_dir: ansible-provider-junos-vqfx-override/configs
    jtaf_commit_confirm_minutes: 5
  tasks:
    - name: Load override + commit confirmed
      juniper.device.config:
        host: "{{ ansible_host }}"
        port: "{{ ansible_port | default(830) }}"
        user: "{{ netconf_user }}"
        passwd: "{{ netconf_pass }}"
        load: override
        src: "{{ tmp_dir }}/{{ inventory_hostname }}.xml"
        confirmed: "{{ jtaf_commit_confirm_minutes }}"
        diff: true
        comment: "JTAF override mode"
      register: override_result

    - name: Show diff applied
      debug:
        var: override_result.diff_lines
      when: override_result.diff_lines | default([]) | length > 0

    - name: Verify device still reachable (port 830)
      wait_for:
        host: "{{ ansible_host }}"
        port: "{{ ansible_port | default(830) }}"
        timeout: 280

    - name: Confirm commit (clear rollback timer)
      juniper.device.config:
        host: "{{ ansible_host }}"
        port: "{{ ansible_port | default(830) }}"
        user: "{{ netconf_user }}"
        passwd: "{{ netconf_pass }}"
        check: true
        diff: false
        commit: false

    - name: Success
      debug:
        msg: "Override applied and confirmed on {{ inventory_hostname }}"
EOF
```

### What to explain
- "The dry-run playbook loads config into the candidate but does NOT commit — safe for previewing"
- "The apply playbook uses `commit confirmed 5` — if the device becomes unreachable after the change, Junos automatically rolls back after 5 minutes"
- "After verifying the device is still reachable, we send a confirming commit to make it permanent"

---

## Phase 4: Dry-Run (Preview Only)

### What we're testing
- Template renders correctly (bare `<configuration>`, no groups)
- NETCONF connectivity works
- We can see what `load override` would change without actually committing

### Commands

```bash
ansible-playbook -i test-inventory.ini test-override-dryrun.yml
```

### Expected Output

```
PLAY [Render config (override mode)] ****
TASK [vqfx-override_role : Initialize explicit variable layers] **** ok
TASK [vqfx-override_role : Load global group vars] **** ok
TASK [vqfx-override_role : Merge group vars for each inventory group] **** ok
TASK [vqfx-override_role : Locate host vars file] **** ok
TASK [vqfx-override_role : Load host vars] **** ok
TASK [vqfx-override_role : Merge variables from hierarchy] **** ok
TASK [vqfx-override_role : Apply custom merge directives] **** ok
TASK [vqfx-override_role : Applying template for vqfx-override_role] **** ok

PLAY [Preview override diff (NO commit)] ****
TASK [Load override (candidate only, NO commit)] **** changed
TASK [Show what would change] ****
    "preview.diff_lines": [
        "[edit]",
        "- version 18.1R3-S5.3;",
        "[edit system services]",
        "-    extension-service { ... }",
    ]
```

### What to explain
- "Play 1 renders the XML from YAML variables — this runs locally, no device connection needed"
- "Play 2 connects to the device via NETCONF, loads the rendered XML with `load override`, and shows the diff"
- "The diff shows what would change — the `version` statement is auto-managed by Junos (always regenerated), and `extension-service` is config on the device that isn't in our YANG model"
- "Crucially: `commit: false` means nothing was changed on the device — this is safe to run anytime"

---

## Phase 5: Apply with Commit Confirmed

### What we're testing
- `load override` actually replaces the device config
- `commit confirmed` timer activates (5-minute auto-rollback safety)
- Device remains reachable after the override
- Confirming commit clears the timer and makes the change permanent

### Commands

```bash
ansible-playbook -i test-inventory.ini test-override-apply.yml
```

### Expected Output

```
TASK [Load override + commit confirmed] **** changed
TASK [Show diff applied] ****
    "override_result.diff_lines": [
        "[edit system services]",
        "-    extension-service { ... }",
    ]
TASK [Verify device still reachable (port 830)] **** ok
TASK [Confirm commit (clear rollback timer)] **** ok
TASK [Success] ****
    "msg": "Override applied and confirmed on dc1-spine1"
```

### What to explain
- "The `load override` pushed our full rendered config to the device"
- "Junos computed the diff internally — it only changed what was different (removed extension-service)"
- "`commit confirmed 5` means: if we can't reach the device within 5 minutes, Junos rolls back automatically"
- "We verified port 830 is still open, then sent the confirming commit — the change is now permanent"
- "This is the safety net: if we accidentally removed the management IP, the device would be unreachable, the timer would expire, and Junos would roll back to the previous config"

---

## Phase 6: Day-2 Change (Edit YAML → Re-Apply)

### What we're testing
- Operators can make changes by editing YAML variables
- Re-running the playbook automatically picks up the changes
- Junos only commits the delta (even though we push the full config)

### Commands

```bash
# Edit the SNMP contact for dc1-spine1
# (Change from "aburston@juniper.net" to "jtaf-demo@juniper.net")
vi ansible_override_files/host_vars/dc1-spine1.yaml
# Find the snmp: contact: line and change the value

# Or use sed:
sed -i 's/aburston@juniper.net/jtaf-demo@juniper.net/' ansible_override_files/host_vars/dc1-spine1.yaml
```

```bash
# Preview the change (dry-run first)
ansible-playbook -i test-inventory.ini test-override-dryrun.yml
```

Expected diff shows ONLY the SNMP contact change:
```
[edit snmp]
-  contact "aburston@juniper.net";
+  contact "jtaf-demo@juniper.net";
```

```bash
# Apply the change
ansible-playbook -i test-inventory.ini test-override-apply.yml
```

### What to explain
- "This is the Day-2 workflow: edit a YAML variable, re-run the playbook"
- "Even though we use `load override` (full config), Junos only commits what actually changed"
- "The diff shows just the SNMP contact line — everything else is identical"
- "No patch engine needed — Junos candidate config IS the diff engine"
- "This is the key advantage over the old group mode: you don't need to think about what's in your group vs what's on the device"

---

## Phase 7: Verify on Device

### Commands

```bash
ssh jcluser@100.123.24.3
```

On the device:
```
# See commit history
show system commit

# See what changed in last commit
show configuration | compare rollback 1

# Verify SNMP contact
show configuration snmp

# Verify interfaces are still up
show interfaces terse | match "ge-|xe-"

# Verify BGP sessions
show bgp summary
```

### What to explain
- "`show configuration | compare rollback 1` shows exactly what changed — should match the diff we saw in the playbook"
- "All interfaces and BGP sessions should still be up — override mode only changed what was different"
- "The commit history shows our commit comment 'JTAF override mode'"

---

## Phase 8: Expand to Full Topology (Optional)

### Commands

```bash
# Create full inventory
cat > full-inventory.ini << 'EOF'
[dc1-borderleaf]
dc1-borderleaf1 ansible_host=100.123.24.1 ansible_port=830
dc1-borderleaf2 ansible_host=100.123.24.2 ansible_port=830

[dc1-spine]
dc1-spine1 ansible_host=100.123.24.3 ansible_port=830
dc1-spine2 ansible_host=100.123.24.4 ansible_port=830

[dc1-leaf]
dc1-leaf1 ansible_host=100.123.24.5 ansible_port=830
dc1-leaf2 ansible_host=100.123.24.6 ansible_port=830
dc1-leaf3 ansible_host=100.123.24.7 ansible_port=830

[dc2-spine]
dc2-spine1 ansible_host=100.123.24.8 ansible_port=830
dc2-spine2 ansible_host=100.123.24.9 ansible_port=830
EOF

# Dry-run all devices
ansible-playbook -i full-inventory.ini test-override-dryrun.yml

# Apply to all devices (if dry-run looks good)
ansible-playbook -i full-inventory.ini test-override-apply.yml
```

---

## Comparison: Group Mode vs Override Mode

For reference, here's how the same workflow looks in group mode (the default/existing behavior):

```bash
# Group mode (existing behavior)
jtaf-yang2ansible ... -t vqfx --mode group

# Template wraps config in:
#   <configuration>
#     <groups><name>JTAF_ANSIBLE</name>...</groups>
#     <apply-groups>JTAF_ANSIBLE</apply-groups>
#   </configuration>

# Playbook uses: load: replace (scoped to the group only)
# Other config on the device is NOT touched
```

```bash
# Override mode (new feature)
jtaf-yang2ansible ... -t vqfx-override --mode override

# Template renders:
#   <configuration>
#     ...bare config...
#   </configuration>

# Playbook uses: load: override + commit confirmed
# EVERYTHING on the device is replaced with what you provide
# commit confirmed = auto-rollback safety net
```

| Aspect | Group Mode | Override Mode |
|--------|-----------|--------------|
| Template | `<groups>JTAF_ANSIBLE</groups>` | Bare `<configuration>` |
| NETCONF operation | `load replace` | `load override` |
| Scope | Only the JTAF group | Entire device config |
| Other config | Preserved | Removed if not in template |
| Safety | Group isolation | `commit confirmed` auto-rollback |
| Day-2 changes | Edit YAML, re-run | Edit YAML, re-run |
| Diff engine | Junos (scoped to group) | Junos (full config) |

---

## Troubleshooting

| Issue | Cause | Fix |
|-------|-------|-----|
| `CommitError: Missing mandatory statement 'ssl'` | Rendered XML has `extension-service/grpc` without required `ssl` | Remove `extension_service` from host_vars/group_vars, or edit rendered XML |
| `ConnectTimeoutError` | Wrong IP or device unreachable | Verify IP in inventory, check `ping` and `nc -zv <ip> 830` |
| `ncclient module could not be imported` | ncclient not installed in the Python Ansible is using | Set `interpreter_python` in ansible.cfg to your venv path |
| `role not found` | Ansible can't find the role directory | Set `roles_path` in ansible.cfg |
| `jtaf_apply_merge_directives not found` | Filter plugins not in path | Set `filter_plugins` in ansible.cfg |
| Device unreachable after apply | Override removed management IP or NETCONF | Wait for commit confirmed timer to expire (auto-rollback) |

---

## Summary

The Ansible Override Mode feature enables:

1. **Full device ownership** — what you provide IS the config
2. **Junos as the diff engine** — no patch engine needed, Junos calculates changes
3. **Commit confirmed safety** — auto-rollback if device becomes unreachable
4. **Simple Day-2 workflow** — edit YAML, re-run playbook, only the diff is committed
5. **Backward compatible** — group mode (default) is unchanged
