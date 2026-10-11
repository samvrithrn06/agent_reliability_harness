test:
	python3 -m unittest discover -s tests -v
run:
	python3 -m agentbench run --seeds 10 --out results
ablation:
	python3 -m agentbench run --designs naive,guarded,din_no_gate_verify,defense_in_depth \
	  --conditions injection_obfuscated,injection_novel --seeds 20 --out results/ablation
sweep:
	python3 -m agentbench sweep --out results --seeds 8
all: test run ablation sweep
