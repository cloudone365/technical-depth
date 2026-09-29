# -*- coding: utf-8 -*-
# Inventory plugin: discover DGX Sparks on the local LAN via mDNS (_ssh._tcp),
# the same mechanism NVIDIA's `discover-sparks` script uses.
from __future__ import annotations

DOCUMENTATION = r"""
name: spark_mdns
short_description: Discover DGX Spark systems via Avahi/mDNS
description:
  - Runs C(avahi-browse -p -r -t _ssh._tcp) on the control node (or reads a saved
    capture) and adds every host whose mDNS name matches O(name_regex).
  - Hosts land in group O(group) with C(ansible_host) set to the discovered IPv4.
options:
  plugin:
    description: Must be C(spark_mdns).
    required: true
    choices: [spark_mdns]
  name_regex:
    description: Regex applied to the mDNS host name (without .local).
    type: str
    default: '^(spark|dgx-spark|spark-)[-\w]*$'
  group:
    description: Group to put discovered hosts in.
    type: str
    default: spark
  interface:
    description: Only accept answers seen on this control-node interface (e.g. the mgmt NIC).
    type: str
  from_file:
    description: Parse this saved avahi-browse -p output instead of running avahi-browse (testing/CI).
    type: path
  timeout:
    description: Seconds to wait for avahi-browse.
    type: int
    default: 10
extends_documentation_fragment:
  - constructed
"""

EXAMPLES = r"""
# inventory/spark.mdns.yml
plugin: spark_mdns
name_regex: '^spark-\d+$'
interface: enp0s31f6
keyed_groups:
  - key: mdns_interface
    prefix: seen_on
"""

import re
import subprocess

from ansible.errors import AnsibleParserError
from ansible.plugins.inventory import BaseInventoryPlugin, Constructable


class InventoryModule(BaseInventoryPlugin, Constructable):
    NAME = "spark_mdns"

    def verify_file(self, path):
        return super().verify_file(path) and path.endswith(("spark.mdns.yml", "spark.mdns.yaml"))

    def _capture(self):
        src = self.get_option("from_file")
        if src:
            with open(src) as fh:
                return fh.read()
        try:
            out = subprocess.run(
                ["avahi-browse", "-p", "-r", "-t", "_ssh._tcp"],
                capture_output=True, text=True, timeout=self.get_option("timeout"),
            )
        except FileNotFoundError as e:
            raise AnsibleParserError("avahi-browse not found: apt install avahi-utils") from e
        except subprocess.TimeoutExpired as e:
            raise AnsibleParserError("avahi-browse timed out") from e
        return out.stdout

    def parse(self, inventory, loader, path, cache=True):
        super().parse(inventory, loader, path, cache)
        self._read_config_data(path)
        name_re = re.compile(self.get_option("name_regex"))
        want_if = self.get_option("interface")
        group = self.inventory.add_group(self.get_option("group"))

        # '=;iface;IPv4;service name;_ssh._tcp;local;host.local;10.10.10.11;22;"txt"'
        for line in self._capture().splitlines():
            f = line.split(";")
            if len(f) < 9 or f[0] != "=" or f[2] != "IPv4":
                continue
            iface, host_fqdn, addr, port = f[1], f[6], f[7], f[8]
            host = host_fqdn.removesuffix(".local")
            if not name_re.search(host) or (want_if and iface != want_if):
                continue
            self.inventory.add_host(host, group=group)
            self.inventory.set_variable(host, "ansible_host", addr)
            self.inventory.set_variable(host, "ansible_port", int(port))
            self.inventory.set_variable(host, "mdns_interface", iface)
            strict = self.get_option("strict")
            hv = self.inventory.get_host(host).get_vars()
            self._set_composite_vars(self.get_option("compose"), hv, host, strict=strict)
            self._add_host_to_composed_groups(self.get_option("groups"), hv, host, strict=strict)
            self._add_host_to_keyed_groups(self.get_option("keyed_groups"), hv, host, strict=strict)
