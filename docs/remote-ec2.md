# Remote EC2 Development

This repo can bootstrap an Ubuntu EC2 machine into a benchmark worker with one
script. Use a CPU instance with a large gp3 EBS volume; SWE-bench does not need
GPU, but Docker images and build caches need disk.

Recommended first machine:

```text
Ubuntu 22.04/24.04
c7i.4xlarge or m7i.4xlarge
300-500 GiB gp3 root volume
```

After cloning:

```bash
git clone https://github.com/ggnnggez/hfa.git
cd hfa
bash scripts/bootstrap_remote_dev.sh
```

If Docker group membership is not active yet, reconnect over SSH or run:

```bash
newgrp docker
docker ps
```

The script writes `.env.bench` with cache/workspace locations. It does not write
API keys. Set the key in the shell or through your secret manager:

```bash
export HERMES_BENCH_API_KEY='...'
```

`.env.bench` bridges that key into Hermes auxiliary provider variables at
runtime:

```bash
KIMI_CN_API_KEY=${HERMES_BENCH_API_KEY}
KIMI_API_KEY=${HERMES_BENCH_API_KEY}
KIMI_BASE_URL=https://api.moonshot.cn/v1
```

Run the SWE-bench Lite smoke:

```bash
head -1 benchmark/families/F2_bug_fix/tasks_swebench_lite.jsonl > /tmp/tasks_swebench_lite_1.jsonl

source .env.bench && uv run --project hermes_v-0-10-0 python -u benchmark/run_benchmark.py \
  --family F2_bug_fix \
  --tasks-file /tmp/tasks_swebench_lite_1.jsonl \
  --config benchmark/configs/f2_swebench_lite_baseline.yaml \
  --runner hermes_direct \
  --out benchmark/results/f2_swebench_lite_smoke_1.jsonl \
  --live-log
```

For larger runs, keep the same `.env.bench` loaded so temporary workspaces,
Hugging Face cache, and uv cache stay off the small default filesystem when a
large `/mnt/bench` volume is available.
