# DCGM exporter on DGX Spark

The GPU Operator from 01-Ansible installs with `dcgmExporter.enabled: false`
because DCGM support for GB10 depends on the DGX OS / DCGM release.

Check first, on the Spark:

    dcgmi discovery -l          # must list "NVIDIA GB10"

If it does, turn it on (01-Ansible lab):

    ansible-playbook playbooks/20.1-gpu-operator.yml -e gpu_operator_dcgm_exporter=true   # or: Semaphore template "20.1 GPU Operator" with that extra variable

then apply manifests/root/95-observability/ (ServiceMonitors + rules + dashboard) with --context spark-root.
If it does not, the 01-Ansible textfile collector (nvidia-smi → node-exporter)
feeds the same dashboard panels via the `spark_gpu_*` metrics.
