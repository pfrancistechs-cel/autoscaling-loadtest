# Diagram prompt (paste into an AI image model)

Create a clean, wide infrastructure diagram, white background, flat icons, thin labelled arrows, a legend at the bottom. Title: "Autoscaling and load testing on one EC2 instance (k3s, HPA, KEDA, k6)".

Outer box: "EC2 instance m7i-flex.large". Inside it, a box "Docker container: k3s (one-node Kubernetes)" containing a namespace box "demo" and a small namespace box "keda".

Inside "demo", two horizontal lanes:

Lane 1 "CPU autoscaling": "Traefik ingress :80" on the left sends traffic to a row of small pod icons "web pods (1 to 6)", drawn as one pod solid and five dashed to show they appear and disappear. Above them a gauge labelled "metrics-server: CPU per pod". A control-loop arrow from a box "HPA (target 70% CPU, every 5 s)" reads the gauge and adjusts the pod count.

Lane 2 "Queue autoscaling": a cylinder "Redis list: jobs" feeding a row of "worker pods (0 to 6)", drawn mostly dashed, with a label "can be zero". A control-loop arrow from a box "KEDA ScaledObject (1 worker per 5 jobs, polls every 5 s)" reads the queue length and sets the worker count. The "keda" namespace box holds "keda-operator" and "keda metrics server", connected to the ScaledObject.

Also inside "demo": a "console" pod reading the Kubernetes API (read-only) and Redis, with an arrow to a browser window showing two charts (pods and CPU, workers and queue).

Outside k3s, on the instance: a "k6" icon with an arrow into Traefik labelled "open model: fixed requests per second, 70 s ramp then 40 s judged" and a verdict badge "p95 < 500 ms and errors < 1%: good / bad". A "scripts/demo.sh" icon runs k6 and kubectl (via docker exec).

Outside the EC2 box: "Your laptop (My IP only)" with an arrow to "port 8080: / (app) and /console/ (live view)".

Note: "Security group inbound: 22 and 8080 from My IP only. No S3, no IAM role."
