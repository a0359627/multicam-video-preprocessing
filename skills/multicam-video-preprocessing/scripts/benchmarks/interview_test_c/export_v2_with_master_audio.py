#!/usr/bin/env python3
"""
Multi-Camera AI Rough-Cut V2 Exporter with Master Audio Integration.
Implements the 3 key editing enhancements:
1. Auto-merge consecutive same-camera cuts (eliminate through-cuts / false cuts).
2. Monologue rhythm injection (insert 3.5s listener reaction shots in shots > 55s).
3. Zero-point alignment (trim the 24.8s countdown pre-roll so timeline starts at 00:00:00:00).
4. Master Audio Integration (6-track audio: A1/A2 OBS Master Mix, A3/A4 CAM1, A5/A6 CAM2).
"""

import csv
import os
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from xml.dom import minidom

FPS = 29.97
TIMEBASE = 30
NTSC = "TRUE"
GLOBAL_START_SEC = 24.800
GLOBAL_START_FRAME = round(GLOBAL_START_SEC * FPS) # 743
REACTION_DURATION_SEC = 3.5

def sec_to_frames(sec):
    return round(float(sec) * FPS)

def frames_to_tc(frames, fps=29.97):
    total_sec = frames / fps
    m = int(total_sec // 60)
    s = int(total_sec % 60)
    f = int(round((total_sec - int(total_sec)) * fps))
    return f"00:{m:02d}:{s:02d}:{f:02d}"

def file_path_to_url(path_str):
    abs_path = os.path.abspath(path_str)
    encoded = urllib.parse.quote(abs_path, safe="/:")
    return f"file://localhost{encoded}"

def load_and_upgrade_edl(edl_csv_path):
    with open(edl_csv_path, "r", encoding="utf-8-sig") as f:
        reader = list(csv.reader(f))
    rows = reader[1:]

    def to_s(t_str):
        parts = t_str.split(":")
        return float(parts[0]) * 60 + float(parts[1])

    # 1. Merge consecutive same-camera cuts
    merged = []
    for r in rows:
        s_sec = to_s(r[0])
        e_sec = to_s(r[1])
        cam = r[2].strip()
        rule = r[3].strip()
        reason = r[4].strip()

        if merged and merged[-1]["cam"] == cam:
            merged[-1]["end_sec"] = e_sec
            merged[-1]["reason"] += " + " + reason
        else:
            merged.append({
                "start_sec": s_sec,
                "end_sec": e_sec,
                "cam": cam,
                "rule": rule,
                "reason": reason
            })

    # 2. Inject listener reaction shots into long monologues (> 55s)
    upgraded = []
    for item in merged:
        dur = item["end_sec"] - item["start_sec"]
        other_cam = "CAM1" if item["cam"] == "CAM2" else "CAM2"
        speaker_name = "工頭堅" if item["cam"] == "CAM2" else "沈伯洋"
        listener_name = "沈伯洋" if item["cam"] == "CAM2" else "工頭堅"

        if dur <= 55:
            upgraded.append(item)
        elif 55 < dur <= 95:
            # 1 reaction shot at midpoint
            mid = item["start_sec"] + dur * 0.5
            r_start = round(mid - REACTION_DURATION_SEC / 2, 3)
            r_end = round(r_start + REACTION_DURATION_SEC, 3)

            upgraded.append({
                "start_sec": item["start_sec"],
                "end_sec": r_start,
                "cam": item["cam"],
                "rule": item["rule"],
                "reason": item["reason"]
            })
            upgraded.append({
                "start_sec": r_start,
                "end_sec": r_end,
                "cam": other_cam,
                "rule": "[升級] 聆聽者反應鏡頭",
                "reason": f"打破{speaker_name}長篇發話視覺疲勞，切入{listener_name}專注聆聽反應"
            })
            upgraded.append({
                "start_sec": r_end,
                "end_sec": item["end_sec"],
                "cam": item["cam"],
                "rule": item["rule"],
                "reason": f"接續{speaker_name}論述主線"
            })
        else: # > 95s
            # 2 reaction shots at 33% and 66%
            t1 = round(item["start_sec"] + dur * 0.33, 3)
            t2 = round(item["start_sec"] + dur * 0.66, 3)

            upgraded.append({
                "start_sec": item["start_sec"],
                "end_sec": t1,
                "cam": item["cam"],
                "rule": item["rule"],
                "reason": item["reason"]
            })
            upgraded.append({
                "start_sec": t1,
                "end_sec": round(t1 + REACTION_DURATION_SEC, 3),
                "cam": other_cam,
                "rule": "[升級] 聆聽者反應鏡頭",
                "reason": f"打破{speaker_name}長篇發話視覺疲勞，切入{listener_name}點頭聆聽"
            })
            upgraded.append({
                "start_sec": round(t1 + REACTION_DURATION_SEC, 3),
                "end_sec": t2,
                "cam": item["cam"],
                "rule": item["rule"],
                "reason": f"接續{speaker_name}深度論述"
            })
            upgraded.append({
                "start_sec": t2,
                "end_sec": round(t2 + REACTION_DURATION_SEC, 3),
                "cam": other_cam,
                "rule": "[升級] 聆聽者反應鏡頭",
                "reason": f"切入{listener_name}思考神態"
            })
            upgraded.append({
                "start_sec": round(t2 + REACTION_DURATION_SEC, 3),
                "end_sec": item["end_sec"],
                "cam": item["cam"],
                "rule": item["rule"],
                "reason": f"收束{speaker_name}該段重點"
            })

    return upgraded

def build_fcp7_xml_v2(
    upgraded_edl,
    cam1_path,
    cam2_path,
    master_audio_path,
    master_offset_sec,
    output_xml_path
):
    """
    Construct Final Cut Pro 7 XML with:
    - Zero-point timeline alignment
    - Upgraded video cut list
    - 6-track audio layout
    """
    first_start_sec = upgraded_edl[0]["start_sec"]
    last_end_sec = upgraded_edl[-1]["end_sec"]
    total_timeline_sec = last_end_sec - first_start_sec
    total_timeline_frames = sec_to_frames(total_timeline_sec)

    # Master audio in point in source file
    master_in_sec = first_start_sec - master_offset_sec
    master_in_frame = sec_to_frames(master_in_sec)
    master_out_frame = master_in_frame + total_timeline_frames

    # Cam 1 & 2 in points
    cam_in_frame = sec_to_frames(first_start_sec)
    cam_out_frame = cam_in_frame + total_timeline_frames

    # Root
    xmeml = ET.Element("xmeml", version="4")
    seq = ET.SubElement(xmeml, "sequence", id="sequence-v2-master-audio")
    ET.SubElement(seq, "name").text = "final_cut_gemini_3.8_v2_with_master_audio"
    ET.SubElement(seq, "duration").text = str(total_timeline_frames)

    rate_elem = ET.SubElement(seq, "rate")
    ET.SubElement(rate_elem, "timebase").text = str(TIMEBASE)
    ET.SubElement(rate_elem, "ntsc").text = NTSC

    tc_elem = ET.SubElement(seq, "timecode")
    tc_rate = ET.SubElement(tc_elem, "rate")
    ET.SubElement(tc_rate, "timebase").text = str(TIMEBASE)
    ET.SubElement(tc_rate, "ntsc").text = NTSC
    ET.SubElement(tc_elem, "string").text = "00:00:00:00"
    ET.SubElement(tc_elem, "frame").text = "0"
    ET.SubElement(tc_elem, "displayformat").text = "NDF"

    media = ET.SubElement(seq, "media")

    # ==================== VIDEO TRACK ====================
    video = ET.SubElement(media, "video")
    v_format = ET.SubElement(video, "format")
    sample_chars = ET.SubElement(v_format, "samplecharacteristics")
    sc_rate = ET.SubElement(sample_chars, "rate")
    ET.SubElement(sc_rate, "timebase").text = str(TIMEBASE)
    ET.SubElement(sc_rate, "ntsc").text = NTSC
    ET.SubElement(sample_chars, "width").text = "1920"
    ET.SubElement(sample_chars, "height").text = "1080"
    ET.SubElement(sample_chars, "pixelaspectratio").text = "square"

    v_track = ET.SubElement(video, "track")

    curr_timeline_frame = 0
    for idx, cut in enumerate(upgraded_edl, start=1):
        cut_dur_sec = cut["end_sec"] - cut["start_sec"]
        cut_dur_frames = sec_to_frames(cut_dur_sec)
        
        src_in_frame = sec_to_frames(cut["start_sec"])
        src_out_frame = src_in_frame + cut_dur_frames

        cam_name = cut["cam"]
        target_fpath = cam1_path if cam_name == "CAM1" else cam2_path
        target_bname = os.path.basename(target_fpath)

        clip_item = ET.SubElement(v_track, "clipitem", id=f"video-item-{idx}")
        ET.SubElement(clip_item, "name").text = f"{cam_name} ({'沈伯洋' if cam_name == 'CAM1' else '工頭堅'})"
        ET.SubElement(clip_item, "enabled").text = "TRUE"
        ET.SubElement(clip_item, "duration").text = str(cut_dur_frames)
        c_rate = ET.SubElement(clip_item, "rate")
        ET.SubElement(c_rate, "timebase").text = str(TIMEBASE)
        ET.SubElement(c_rate, "ntsc").text = NTSC

        ET.SubElement(clip_item, "start").text = str(curr_timeline_frame)
        ET.SubElement(clip_item, "end").text = str(curr_timeline_frame + cut_dur_frames)
        ET.SubElement(clip_item, "in").text = str(src_in_frame)
        ET.SubElement(clip_item, "out").text = str(src_out_frame)

        # File element
        file_elem = ET.SubElement(clip_item, "file", id=f"masterclip-{cam_name}")
        ET.SubElement(file_elem, "name").text = target_bname
        ET.SubElement(file_elem, "pathurl").text = file_path_to_url(target_fpath)
        f_rate = ET.SubElement(file_elem, "rate")
        ET.SubElement(f_rate, "timebase").text = str(TIMEBASE)
        ET.SubElement(f_rate, "ntsc").text = NTSC
        ET.SubElement(file_elem, "duration").text = "140000"

        f_media = ET.SubElement(file_elem, "media")
        f_video = ET.SubElement(f_media, "video")
        f_v_sample = ET.SubElement(f_video, "samplecharacteristics")
        f_v_rate = ET.SubElement(f_v_sample, "rate")
        ET.SubElement(f_v_rate, "timebase").text = str(TIMEBASE)
        ET.SubElement(f_v_rate, "ntsc").text = NTSC
        ET.SubElement(f_v_sample, "width").text = "1920"
        ET.SubElement(f_v_sample, "height").text = "1080"
        ET.SubElement(f_v_sample, "pixelaspectratio").text = "square"

        # Marker
        marker = ET.SubElement(clip_item, "marker")
        ET.SubElement(marker, "name").text = cut["rule"]
        ET.SubElement(marker, "comment").text = cut["reason"]
        ET.SubElement(marker, "in").text = str(src_in_frame)
        ET.SubElement(marker, "out").text = str(src_in_frame + 1)
        ET.SubElement(marker, "rgb").text = "(0,180,255)" if "反應" in cut["rule"] else "(255,100,0)"

        curr_timeline_frame += cut_dur_frames

    # ==================== AUDIO TRACKS (6 TRACKS) ====================
    audio = ET.SubElement(media, "audio")

    def add_audio_track(track_name, fpath, file_id, in_f, out_f, ch_idx, clip_id):
        a_track = ET.SubElement(audio, "track")
        c_item = ET.SubElement(a_track, "clipitem", id=clip_id)
        ET.SubElement(c_item, "name").text = track_name
        ET.SubElement(c_item, "enabled").text = "TRUE"
        ET.SubElement(c_item, "duration").text = str(total_timeline_frames)
        r = ET.SubElement(c_item, "rate")
        ET.SubElement(r, "timebase").text = str(TIMEBASE)
        ET.SubElement(r, "ntsc").text = NTSC
        ET.SubElement(c_item, "start").text = "0"
        ET.SubElement(c_item, "end").text = str(total_timeline_frames)
        ET.SubElement(c_item, "in").text = str(in_f)
        ET.SubElement(c_item, "out").text = str(out_f)

        f_elem = ET.SubElement(c_item, "file", id=file_id)
        ET.SubElement(f_elem, "name").text = os.path.basename(fpath)
        ET.SubElement(f_elem, "pathurl").text = file_path_to_url(fpath)
        fr = ET.SubElement(f_elem, "rate")
        ET.SubElement(fr, "timebase").text = str(TIMEBASE)
        ET.SubElement(fr, "ntsc").text = NTSC
        ET.SubElement(f_elem, "duration").text = "140000"

        fm = ET.SubElement(f_elem, "media")
        fa = ET.SubElement(fm, "audio")
        fas = ET.SubElement(fa, "samplecharacteristics")
        ET.SubElement(fas, "depth").text = "16"
        ET.SubElement(fas, "samplerate").text = "48000"
        ET.SubElement(fa, "channelcount").text = "2"

        src_tr = ET.SubElement(c_item, "sourcetrack")
        ET.SubElement(src_tr, "mediatype").text = "audio"
        ET.SubElement(src_tr, "trackindex").text = str(ch_idx)

    # A1 & A2: Master Mix (OBS.mkv)
    add_audio_track("Master Mix L (現場混音)", master_audio_path, "masterclip-OBS", master_in_frame, master_out_frame, 1, "audio-master-L")
    add_audio_track("Master Mix R (現場混音)", master_audio_path, "masterclip-OBS", master_in_frame, master_out_frame, 2, "audio-master-R")

    # A3 & A4: CAM1 (沈伯洋)
    add_audio_track("CAM1 Audio L (沈伯洋)", cam1_path, "masterclip-CAM1-audio", cam_in_frame, cam_out_frame, 1, "audio-cam1-L")
    add_audio_track("CAM1 Audio R (沈伯洋)", cam1_path, "masterclip-CAM1-audio", cam_in_frame, cam_out_frame, 2, "audio-cam1-R")

    # A5 & A6: CAM2 (工頭堅)
    add_audio_track("CAM2 Audio L (工頭堅)", cam2_path, "masterclip-CAM2-audio", cam_in_frame, cam_out_frame, 1, "audio-cam2-L")
    add_audio_track("CAM2 Audio R (工頭堅)", cam2_path, "masterclip-CAM2-audio", cam_in_frame, cam_out_frame, 2, "audio-cam2-R")

    # Write formatted XML
    xml_str = ET.tostring(xmeml, encoding="utf-8")
    parsed = minidom.parseString(xml_str)
    pretty_xml = parsed.toprettyxml(indent="    ", encoding="utf-8")

    # Replace doctype header
    header = b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
    content_without_decl = pretty_xml.split(b"\n", 1)[1] if pretty_xml.startswith(b"<?xml") else pretty_xml

    with open(output_xml_path, "wb") as f:
        f.write(header + content_without_decl)

    print(f"✓ Successfully exported V2 XML with Master Audio: {output_xml_path}")
    print(f"  • Total Cuts: {len(upgraded_edl)}")
    print(f"  • Total Timeline Duration: {frames_to_tc(total_timeline_frames)} ({total_timeline_sec:.2f}s)")
    print(f"  • Master Audio Sync Offset: {master_offset_sec:+.3f}s (In: {master_in_sec:.3f}s / frame {master_in_frame})")

def main():
    edl_csv = "/Volumes/Crucial X10/muticam_test_file/output/edl_gemini_3.8.csv"
    cam1_path = "/Volumes/Crucial X10/muticam_test_file/output/CAM1_synced.mp4"
    cam2_path = "/Volumes/Crucial X10/muticam_test_file/output/CAM2_synced.mp4"
    obs_master_wav = "/Volumes/Crucial X10/muticam_test_file/output/OBS_master_audio.wav"
    
    # Measured sub-millisecond offset: CAM1 vs OBS.mkv
    offset_sec = 18.268

    output_csv = "/Volumes/Crucial X10/muticam_test_file/output/edl_gemini_3.8_v2.csv"
    output_xml = "/Volumes/Crucial X10/muticam_test_file/output/final_cut_gemini_3.8_v2_with_master_audio.xml"

    print("================================================================================")
    print("🚀 Generating Multi-Camera AI Rough-Cut V2 with Master Audio Integration")
    print("================================================================================")
    
    upgraded_edl = load_and_upgrade_edl(edl_csv)

    # Save upgraded EDL CSV
    with open(output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Start_Time", "End_Time", "Best_Camera", "剪輯規則", "剪輯原因"])
        for c in upgraded_edl:
            def to_tc(s):
                m = int(s // 60)
                sec = s % 60
                return f"{m:02d}:{sec:06.3f}"
            writer.writerow([to_tc(c["start_sec"]), to_tc(c["end_sec"]), c["cam"], c["rule"], c["reason"]])
    print(f"✓ Saved Upgraded EDL CSV: {output_csv} ({len(upgraded_edl)} cuts)")

    build_fcp7_xml_v2(
        upgraded_edl=upgraded_edl,
        cam1_path=cam1_path,
        cam2_path=cam2_path,
        master_audio_path=obs_master_wav,
        master_offset_sec=offset_sec,
        output_xml_path=output_xml
    )

if __name__ == "__main__":
    main()
