# Sensitivity: attack success rate vs. simulated model susceptibility

p_follow_injection = P(model obeys an unfenced injected instruction). 8 seeds x 25 tasks per cell; ASR = attacks succeeding / runs with >=1 payload.

| p_follow | design | ASR plain | ASR obfuscated | ASR novel |
|---|---|---|---|---|
| 0.2 | naive | 23.9% [17,33] | 23.9% [17,33] | 23.9% [17,33] |
| 0.2 | guarded | 0.0% [0,3] | 4.1% [2,9] | 4.1% [2,9] |
| 0.2 | din_no_gate_verify | 0.0% [0,2] | 0.0% [0,2] | 2.6% [1,7] |
| 0.2 | defense_in_depth | 0.0% [0,2] | 0.0% [0,2] | 1.3% [0,5] |
| 0.55 | naive | 60.6% [51,69] | 60.6% [51,69] | 60.6% [51,69] |
| 0.55 | guarded | 0.0% [0,3] | 13.2% [8,20] | 13.2% [8,20] |
| 0.55 | din_no_gate_verify | 0.0% [0,2] | 0.0% [0,2] | 9.3% [6,15] |
| 0.55 | defense_in_depth | 0.0% [0,2] | 0.0% [0,2] | 5.3% [3,10] |
| 0.9 | naive | 89.9% [83,94] | 89.9% [83,94] | 89.9% [83,94] |
| 0.9 | guarded | 0.0% [0,3] | 21.5% [15,30] | 21.5% [15,30] |
| 0.9 | din_no_gate_verify | 0.0% [0,2] | 0.0% [0,2] | 16.6% [11,23] |
| 0.9 | defense_in_depth | 0.0% [0,2] | 0.0% [0,2] | 9.9% [6,16] |
