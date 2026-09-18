"""Make a silent 60 fps game-time edit from acknowledged source frame indices."""
import argparse
from bisect import bisect_right
import json
from pathlib import Path
import subprocess
import recording as r


def public_notes(folder, generation):
    path=r.ROOT/'runtime/editorial-notes.jsonl'
    if not path.exists():
        return []
    rows=[json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    return sorted((row for row in rows if row.get('recording')==folder.name
                   and row.get('generation')==generation and row.get('public_caption')),
                  key=lambda row:row['tick'])


def caption_frame(frame, game_tick, notes):
    # The original game render occupies the first680 rows. Only replace the
    # recorder's existing40-pixel caption footer, never gameplay pixels.
    result=frame.copy()
    result[680:720]=0
    text='GPT-6 Astra | Recorded tool-assisted play'
    for note in notes:
        if note['tick'] <= game_tick < note['tick'] + 1440:
            text='GPT-6 Astra | '+note['public_caption']
    scale=.8
    while r.cv2.getTextSize(text,r.cv2.FONT_HERSHEY_SIMPLEX,scale,1)[0][0]>1240 and scale>.35:
        scale-=.025
    r.cv2.putText(result,text,(20,706),r.cv2.FONT_HERSHEY_SIMPLEX,scale,
                  (230,235,245),1,r.cv2.LINE_AA)
    return result


def build(folder, generation, output, title, until_tick=None, title_detail=None, ending_lines=None,
          intro_seconds=2, ending_seconds=5):
    folder, output = Path(folder).resolve(), Path(output).resolve()
    rows = [json.loads(line) for line in (folder / 'encoded-game-frames.jsonl').read_text().splitlines()]
    rows = [row for row in rows if row['generation'] == generation
            and (until_tick is None or row['tick']<=until_tick)]
    if len(rows) < 2 or any(b['tick'] <= a['tick'] for a, b in zip(rows, rows[1:])):
        raise RuntimeError('Need one strictly increasing recorded attempt')
    if until_tick is not None and rows[-1]['tick']!=until_tick:
        raise RuntimeError('The requested ending must be an acknowledged source tick')
    notes=public_notes(folder,generation)
    gaps = [b['tick'] - a['tick'] for a, b in zip(rows, rows[1:])]
    if max(gaps) > 4:
        raise RuntimeError('A gameplay frame is missing from the source journal')
    ticks = [row['tick'] for row in rows]
    chosen = []
    chosen_ticks = []
    for tick in range(ticks[0], ticks[-1] + 1, 4):
        row = rows[bisect_right(ticks, tick) - 1]
        chosen.append(row['encoded_frame'] - 1)
        chosen_ticks.append(tick)
    if chosen[-1] != rows[-1]['encoded_frame'] - 1:
        chosen.append(rows[-1]['encoded_frame'] - 1)
        chosen_ticks.append(rows[-1]['tick'])
    if any(b < a for a, b in zip(chosen, chosen[1:])):
        raise RuntimeError('Source frame order must be preserved')

    cap = r.cv2.VideoCapture(str(folder / 'raw.mkv'))
    width, height = int(cap.get(r.cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(r.cv2.CAP_PROP_FRAME_HEIGHT))
    if (width, height) != (1280, 720):
        raise RuntimeError('Unexpected game-only source dimensions')
    if output.exists():
        raise RuntimeError('Refusing to replace an existing edit')
    output.parent.mkdir(parents=True, exist_ok=True)
    log = output.with_suffix('.encoder.log').open('wb')
    command = [str(r.FFMPEG), '-hide_banner', '-loglevel', 'warning', '-n',
               '-f', 'rawvideo', '-pixel_format', 'bgr24', '-video_size', '1280x720',
               '-framerate', '60', '-i', 'pipe:0', '-map', '0:v:0', '-an',
               '-map_metadata', '-1', '-c:v', 'h264_nvenc', '-preset', 'p4', '-cq', '20',
               '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(output)]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                               stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
    title_card = r.np.full((720, 1280, 3), (18, 20, 28), dtype=r.np.uint8)
    r.cv2.line(title_card,(170,190),(1110,190),(185,225,95),4)
    title_lines=[(title, 295, 2.0), ('GPT-6 Astra | Tool-assisted gameplay', 395, 1.05),
                 ('Planning pauses removed | No audio', 465, .85)]
    if title_detail:
        title_lines.append((title_detail,535,.8))
    for text, y, scale in title_lines:
        while r.cv2.getTextSize(text,r.cv2.FONT_HERSHEY_SIMPLEX,scale,2)[0][0]>1180:
            scale-=.025
        size = r.cv2.getTextSize(text, r.cv2.FONT_HERSHEY_SIMPLEX, scale, 2)[0]
        r.cv2.putText(title_card, text, ((1280 - size[0]) // 2, y),
                      r.cv2.FONT_HERSHEY_SIMPLEX, scale, (238, 239, 245), 2, r.cv2.LINE_AA)
    try:
        intro_frames=round(60*intro_seconds)
        for _ in range(intro_frames):
            encoder.stdin.write(title_card.tobytes())
        first = chosen[0]
        if not cap.set(r.cv2.CAP_PROP_POS_FRAMES, first) or abs(cap.get(r.cv2.CAP_PROP_POS_FRAMES) - first) > .1:
            raise RuntimeError('Could not seek to the recorded attempt boundary')
        source_index, last_index, image = first, None, None
        skipped_pause_seeks=0
        for count, target in enumerate(chosen):
            if target != last_index:
                if target-source_index > 240:
                    # Planning can leave minutes of identical source frames.
                    # Seek to the exact acknowledged frame instead of decoding
                    # the entire pause. Selected gameplay frames are unchanged.
                    if not cap.set(r.cv2.CAP_PROP_POS_FRAMES,target) or abs(cap.get(r.cv2.CAP_PROP_POS_FRAMES)-target)>.1:
                        raise RuntimeError('Could not seek across a planning pause')
                    source_index=target
                    skipped_pause_seeks+=1
                while source_index <= target:
                    if not cap.grab():
                        raise RuntimeError('Source ended before a recorded gameplay frame')
                    source_index += 1
                ok, image = cap.retrieve()
                if not ok:
                    raise RuntimeError('Could not decode a selected gameplay frame')
                last_index = target
            decorated=caption_frame(image,chosen_ticks[count],notes)
            encoder.stdin.write(decorated.tobytes())
            if count % 600 == 0:
                print(json.dumps({'edited_seconds': round(count / 60, 1), 'source_frame': target}), flush=True)
        for _ in range(180):
            encoder.stdin.write(decorated.tobytes())
        ending_frames=0
        if ending_lines:
            if len(ending_lines)>5:
                raise ValueError('The ending card supports at most five short lines')
            card=r.np.full((720,1280,3),(18,20,28),dtype=r.np.uint8)
            r.cv2.line(card,(200,185),(1080,185),(185,225,95),4)
            for index,text in enumerate(ending_lines):
                scale=1.85 if index==0 else .9
                while r.cv2.getTextSize(text,r.cv2.FONT_HERSHEY_SIMPLEX,scale,2)[0][0]>1180:
                    scale-=.025
                size=r.cv2.getTextSize(text,r.cv2.FONT_HERSHEY_SIMPLEX,scale,2)[0]
                r.cv2.putText(card,text,((1280-size[0])//2,265+index*70),
                              r.cv2.FONT_HERSHEY_SIMPLEX,scale,(238,239,245),2,r.cv2.LINE_AA)
            ending_frames=round(60*ending_seconds)
            for _ in range(ending_frames):
                encoder.stdin.write(card.tobytes())
        encoder.stdin.close()
        if encoder.wait(timeout=30):
            raise RuntimeError('Silent edit encoder failed; inspect its log')
    finally:
        cap.release()
        if encoder.poll() is None:
            encoder.kill()
            encoder.wait(timeout=10)
        log.close()
    report = {'generation': generation, 'source_game_ticks': [ticks[0], ticks[-1]],
              'source_frame_range': [chosen[0], chosen[-1]], 'maximum_tick_gap': max(gaps),
              'gameplay_frames': len(chosen), 'duration_seconds': (len(chosen) + intro_frames + 180 + ending_frames) / 60,
              'title_detail':title_detail,'ending_card_lines':ending_lines,
              'skipped_pause_seeks':skipped_pause_seeks,
              'public_strategy_captions':len([n for n in notes if ticks[0]<=n['tick']<=ticks[-1]]),
              'method': 'Recorded game frames resampled to game time; planning pauses removed',
              **r.verify_video(output)}
    r.atomic_json(output.with_suffix('.verification.json'), report)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('folder')
    parser.add_argument('generation', type=int)
    parser.add_argument('output')
    parser.add_argument('--title', required=True)
    parser.add_argument('--until-tick',type=int,help='End at the verified completion tick before menu navigation')
    args = parser.parse_args()
    build(args.folder, args.generation, args.output, args.title, args.until_tick)
