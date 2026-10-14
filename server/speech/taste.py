"""Fixed-voice A/B profiles for explicit user listening, not a default style change."""
from copy import deepcopy

STYLES = {
    'natural': ('自然', '语气自然随意，像熟人聊天，不要播音腔，不刻意表演', '+0%', '+0Hz'),
    'serious': ('认真', '认真清楚地解释，节奏稍慢，不要低沉威严，不要说教', '-6%', '+0Hz'),
    'light': ('轻松', '语气轻松，有一点笑意，不过度兴奋，不挖苦', '+5%', '+2Hz'),
    'gentle': ('温和', '温和放松，语速稍慢，不带歉意、不委屈，不刻意气声', '-7%', '-2Hz'),
}

def catalog(engine):
    base = deepcopy(engine.profile('base'))
    voice = base.get('voice') or 'zh-CN-XiaoxiaoNeural'
    if engine.engine != 'stepaudio' and not voice.startswith('zh-CN'):
        voice = 'zh-CN-XiaoxiaoNeural'
    effective = engine.engine if engine.engine in ('stepaudio', 'silent') else 'edge_tts'
    return {'engine': effective, 'voice': voice,
            'controls': 'instruction' if effective == 'stepaudio' else 'rate/pitch' if effective == 'edge_tts' else 'none',
            'styles': [{'id': key, 'label': values[0]} for key, values in STYLES.items()]}

def profile(engine, style):
    if style not in STYLES:
        raise ValueError('unknown style')
    result = deepcopy(engine.profile('base'))
    _, instruction, rate, pitch = STYLES[style]
    result.update(voice=catalog(engine)['voice'], instruction=instruction, rate=rate, pitch=pitch)
    return result
