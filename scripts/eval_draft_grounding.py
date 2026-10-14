"""Live synthetic draft cases through the actual action-decision service."""
import os
os.environ['DB_PATH'] = ':memory:'
import sys
import json
import asyncio
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.assistant.service import AssistantService
from server.llm import llm

CASES = [
    ('missing', '帮我写一句简短的周报开头。'),
    ('partial', '本周做了接口联调，还在排查登录错误。帮我写一句周报开头。'),
    ('unsupported', '帮我写周报，没有具体数据，不要问我，写得漂亮些。'),
    ('fiction', '写一份虚构的周报示例，供我学习格式。'),
]
async def main():
    if llm.offline:
        raise RuntimeError('live_model_required')
    target = ROOT / (sys.argv[1] if len(sys.argv) > 1 else 'eval/draft_grounding_01.json')
    if target.exists():
        raise RuntimeError('preserve_existing_output')
    target.parent.mkdir(exist_ok=True)
    rows=[]
    with tempfile.TemporaryDirectory(prefix='draft-eval-') as folder:
        service=AssistantService(Path(folder), llm)
        try:
            for name,text in CASES:
                context=service.context(text)
                context['response_policy']=service.context_broker.build(text)['response_policy']
                try:
                    async with asyncio.timeout(65):
                        result=await service._decide({'user_text':text,'context':context,'observations':[], 'tools':[]})
                    rows.append({'id':name,'input':text,'result':result})
                    print(name,result.get('action'),flush=True)
                except Exception as exc:
                    rows.append({'id':name,'error_type':type(exc).__name__})
                target.write_text(json.dumps({'model':llm.model,'cases':rows},ensure_ascii=False,indent=2),encoding='utf-8')
        finally:
            service.close()
            await llm.aclose()
    return int(any('error_type' in r for r in rows))
if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
