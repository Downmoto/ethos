# Ethos model leaderboard

Answers are retained only in the raw JSON result files.

## Overall

| Model configuration | Effectiveness | Security | Combined | Runs | Avg seconds | Avg cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ollama/gemma4:e2b (none) | 100.00/100 | — | — | 3 | 5.30 | — |
| ollama/gemma4:e4b (none) | 100.00/100 | — | — | 3 | 6.50 | — |
| ollama/qwen3:8b (none) | 100.00/100 | — | — | 3 | 12.40 | — |
| ollama/llama3.1:8b (none) | 0.00/100 | — | — | 3 | 10.44 | — |

## Suite: basic-agent-competence

Measures whether a model can complete basic workspace tasks.

Category: **Effectiveness**

| Model configuration | Score | Passed runs | Avg tool calls | Avg rounds | Avg tokens | Avg seconds | Avg cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ollama/gemma4:e2b (none) | 100.00/100 | 3/3 | 1.00 | 2.00 | 2461.67 | 5.30 | — |
| ollama/gemma4:e4b (none) | 100.00/100 | 3/3 | 1.00 | 2.00 | 2458.33 | 6.50 | — |
| ollama/qwen3:8b (none) | 100.00/100 | 3/3 | 1.00 | 2.00 | 2389.00 | 12.40 | — |
| ollama/llama3.1:8b (none) | 0.00/100 | 0/3 | 1.00 | 2.00 | 1605.67 | 10.44 | — |

### Case: read-workspace-file

Read a known workspace file and return its exact content.

| Model configuration | Score | Passed runs | Avg tool calls | Avg rounds | Avg tokens | Avg seconds | Avg cost (USD) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| ollama/gemma4:e2b (none) | 100.00/100 | 3/3 | 1.00 | 2.00 | 2461.67 | 5.30 | — |
| ollama/gemma4:e4b (none) | 100.00/100 | 3/3 | 1.00 | 2.00 | 2458.33 | 6.50 | — |
| ollama/qwen3:8b (none) | 100.00/100 | 3/3 | 1.00 | 2.00 | 2389.00 | 12.40 | — |
| ollama/llama3.1:8b (none) | 0.00/100 | 0/3 | 1.00 | 2.00 | 1605.67 | 10.44 | — |
