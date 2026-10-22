# Recorded demo runs

The UI's **Replay** button re-plays a recorded pipeline run: the real events of an
earlier live run, re-emitted with the waits on model calls shortened. It makes the
demo instant and reliable (no model calls), and the UI always labels it as a replay.

To record one, run a sample live (from the UI or the CLI) and copy its saved run:

```bash
python -m scripts.run_pipeline samples/shoplite-coupon.mp4 --no-pr
cp runs/pipeline/<run_id>.json demo/coupon.json      # file name = sample name
```

Only real runs belong here.
