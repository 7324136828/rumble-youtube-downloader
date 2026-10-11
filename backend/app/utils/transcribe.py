"""Whisper subprocess entry point for source checkouts and portable builds."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("WHISPER_MODEL") or "base")
    parser.add_argument("--device", default=os.environ.get("WHISPER_DEVICE") or "cpu")
    args = parser.parse_args(argv)
    try:
        import torch
        import whisper

        if args.model not in whisper.available_models():
            raise ValueError("WHISPER_MODEL must name an available Whisper model, such as base or small")
        if not args.input.is_file():
            raise ValueError("The audio file is missing")
        # A small CPU thread count avoids oversubscribing typical desktop CPUs.
        if args.device == "cpu":
            torch.set_num_threads(min(4, os.cpu_count() or 1))
        model = whisper.load_model(args.model, device=args.device,
                                   download_root=str(args.model_dir))
        result = model.transcribe(str(args.input), fp16=args.device != "cpu",
                                  verbose=False, task="transcribe")
        text = (result.get("text") or "").strip()
        if not text:
            raise ValueError("Whisper did not detect any speech in this media")
        args.output.write_text(json.dumps({"text": text, "language": result.get("language")},
                                          ensure_ascii=False), encoding="utf-8")
        return 0
    except ImportError:
        print("Whisper is not installed. Install openai-whisper or rebuild the portable app with dependencies.",
              flush=True)
        return 1
    except Exception as error:
        print(f"Whisper could not create a transcript: {error}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
