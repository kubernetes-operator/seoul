Kubernetes control plane is running at https://kubernetes:6443

To further debug and diagnose cluster problems, use 'kubectl cluster-info dump'.
---nodes---
NAME              STATUS   ROLES           AGE   VERSION   INTERNAL-IP      EXTERNAL-IP   OS-IMAGE             KERNEL-VERSION      CONTAINER-RUNTIME
infra01           Ready    infra           95d   v1.34.3   192.168.78.103   <none>        Ubuntu 24.04.4 LTS   6.8.0-107-generic   containerd://2.2.1
infra02           Ready    infra           95d   v1.34.3   192.168.78.104   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   containerd://2.2.1
playcekubectr01   Ready    control-plane   95d   v1.34.3   192.168.77.101   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   containerd://2.2.1
playcekubectr02   Ready    control-plane   95d   v1.34.3   192.168.77.102   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   containerd://2.2.1
playcekubectr03   Ready    control-plane   95d   v1.34.3   192.168.77.103   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   containerd://2.2.1
playcekubewrk01   Ready    <none>          95d   v1.34.3   192.168.78.101   <none>        Ubuntu 24.04.4 LTS   6.8.0-106-generic   containerd://2.2.1
playcekubewrk02   Ready    <none>          95d   v1.34.3   192.168.78.102   <none>        Ubuntu 24.04.4 LTS   6.17.0-22-generic   containerd://2.2.1
playcekubewrk03   Ready    <none>          65d   v1.34.3   192.168.78.105   <none>        Ubuntu 24.04.4 LTS   6.17.0-23-generic   containerd://2.2.1
