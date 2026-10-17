"""Run the JurisFlow LoRA adapter through vLLM on the same host as FastAPI."""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

ADAPTER_ID = "grant88/qwen3-8b-jurisflow-qlora-mixed-adapter"


def build_command(adapter_path: Path, args: argparse.Namespace) -> list[str]:
    config = json.loads((adapter_path / "adapter_config.json").read_text())
    if config.get("peft_type") != "LORA":
        raise ValueError("Expected a PEFT LORA adapter.")
    base_model = args.base_model or config.get("base_model_name_or_path")
    if not base_model:
        raise ValueError("Missing base model; specify --base-model with its HF ID.")
    ranks = [config["r"], *config.get("rank_pattern", {}).values()]
    rank = max(ranks)
    supported = (8, 16, 32, 64, 128, 256, 320, 512)
    max_rank = next((value for value in supported if value >= rank), None)
    if max_rank is None:
        raise ValueError(f"Unsupported LoRA rank: {rank}")
    return [
        sys.executable,
        "-m",
        "vllm.entrypoints.openai.api_server",
        "--model",
        base_model,
        "--tokenizer",
        args.tokenizer or base_model,
        "--host",
        "127.0.0.1",
        "--port",
        str(args.port),
        "--enable-lora",
        "--lora-modules",
        json.dumps(
            {
                "name": "jurisflow",
                "path": str(adapter_path),
                "base_model_name": base_model,
            }
        ),
        "--max-lora-rank",
        str(max_rank),
        "--dtype",
        "auto",
        "--max-model-len",
        str(args.max_model_len),
        "--gpu-memory-utilization",
        str(args.gpu_memory_utilization),
        "--max-num-seqs",
        str(args.max_num_seqs),
        "--generation-config",
        "vllm",
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", default=ADAPTER_ID)
    parser.add_argument(
        "--revision", default="main", help="Adapter commit SHA or branch"
    )
    parser.add_argument("--base-model", help="Override adapter's recorded base model")
    parser.add_argument(
        "--tokenizer", help="Override tokenizer if training customized it"
    )
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--max-model-len", type=int, default=4096)
    parser.add_argument("--max-num-seqs", type=int, default=4)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument(
        "--dry-run", action="store_true", help="Resolve adapter and print command"
    )
    args = parser.parse_args()
    if not 0 < args.gpu_memory_utilization < 1:
        parser.error("--gpu-memory-utilization must be between 0 and 1")
    if min(args.max_model_len, args.max_num_seqs) < 1 or not 1 <= args.port <= 65535:
        parser.error("Invalid context size, concurrency, or port")
    path = Path(args.adapter).expanduser()
    if not path.is_dir():
        from huggingface_hub import snapshot_download

        try:
            path = Path(
                snapshot_download(
                    args.adapter,
                    revision=args.revision,
                    allow_patterns=[
                        "adapter_config.json",
                        "adapter_model.safetensors",
                        "adapter_model.bin",
                    ],
                )
            )
        except Exception as exc:
            raise SystemExit(
                "Adapter download failed. Check the repository ID, access permission, "
                "HF_TOKEN, and network connectivity."
            ) from exc
    if not any(
        (path / name).is_file()
        for name in ("adapter_model.safetensors", "adapter_model.bin")
    ):
        parser.error("Adapter weights are missing")
    command = build_command(path.resolve(), args)
    print(shlex.join(command), flush=True)
    if not args.dry_run:
        os.execv(sys.executable, command)


if __name__ == "__main__":
    main()
