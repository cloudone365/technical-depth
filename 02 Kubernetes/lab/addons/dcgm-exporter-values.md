# DCGM exporter on DGX Spark

The GPU Operator from 01 Ansible installs with `dcgmExporter.enabled: false`
because DCGM support for GB10 depends on the DGX OS / DCGM release.

Check first, on the Spark:

    dcgmi discovery -l          # must list "NVIDIA GB10"

If it does, turn it on (01 Ansible lab):

    ansible-playbook playbooks/06-gpu-operator.yml -e gpu_operator_dcgm_exporter=true

then apply manifests/95-observability/ (ServiceMonitor + rules + dashboard).
If it does not, the 01 Ansible textfile collector (nvidia-smi → node-exporter)
feeds the same dashboard panels via the `spark_gpu_*` metrics.
