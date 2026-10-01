#!/usr/bin/env python3
"""
Test C Exporter: Gemini 3.8 Flash Pure Video Understanding Rough-Cut
===================================================================
1. Loads edl_gemini_3.8_c.csv (103 cuts autonomously decided by Gemini 3.8 Flash).
2. Merges any residual consecutive same-camera cuts.
3. Zero-aligns timeline to 00:00:00:00 from Global Start Time (00:24.500).
4. Assembles 6-track audio architecture:
   - A1/A2: OBS Master Mix (OBS_master_audio.wav, sub-millisecond sync)
   - A3/A4: CAM1 沈伯洋 (isolated original audio)
   - A5/A6: CAM2 工頭堅 (isolated original audio)
5. Exports broadcast-ready FCP7 XML: final_cut_gemini_3.8_c_with_master_audio.xml
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

def to_sec(t_str):
    parts = t_str.strip().split(":")
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return 0.0

def load_and_clean_c_edl(edl_csv_path):
    with open(edl_csv_path, "r", encoding="utf-8-sig") as f:
        reader = list(csv.reader(f))
    rows = reader[1:]

    cuts = []
    for r in rows:
        cuts.append({
            "start_sec": to_sec(r[0]),
            "end_sec": to_sec(r[1]),
            "cam": r[2].strip(),
            "rule": r[3].strip(),
            "reason": r[4].strip()
        })

    # Merge consecutive same-camera cuts
    cleaned = []
    for c in cuts:
        if cleaned and cleaned[-1]["cam"] == c["cam"]:
            cleaned[-1]["end_sec"] = c["end_sec"]
            cleaned[-1]["reason"] += " + " + c["reason"]
        else:
            cleaned.append(c)

    return cleaned

def build_fcp7_xml_c(
    edl_cuts,
    cam1_path,
    cam2_path,
    master_audio_path,
    master_offset_sec,
    output_xml_path
):
    global_start_sec = edl_cuts[0]["start_sec"]
    global_end_sec = edl_cuts[-1]["end_sec"]
    
    total_duration_sec = global_end_sec - global_start_sec
    total_duration_frames = sec_to_frames(total_duration_sec)
    
    global_start_frame = sec_to_frames(global_start_sec)
    global_end_frame = global_start_frame + total_duration_frames

    print(f"  • Global Start Sec  : {global_start_sec:.3f}s (Frame {global_start_frame})")
    print(f"  • Global End Sec    : {global_end_sec:.3f}s (Frame {global_end_frame})")
    print(f"  • Total Runtime     : {total_duration_sec:.3f}s ({total_duration_frames} frames / {frames_to_tc(total_duration_frames)})")
    print(f"  • Master Audio Sync : +{master_offset_sec:.3f}s offset")

    # XML Root
    xsequence = ET.Element("sequence", id="sequence-TestC-Gemini38")
    ET.SubElement(xsequence, "name").text = "Interview Multi-Cam Test C (Gemini 3.8 Flash Pure AI + Master Audio)"
    ET.SubElement(xsequence, "duration").text = str(total_duration_frames)

    xrate = ET.SubElement(xsequence, "rate")
    ET.SubElement(xrate, "timebase").text = str(TIMEBASE)
    ET.SubElement(xrate, "ntsc").text = NTSC

    xtimecode = ET.SubElement(xsequence, "timecode")
    xrate_tc = ET.SubElement(xtimecode, "rate")
    ET.SubElement(xrate_tc, "timebase").text = str(TIMEBASE)
    ET.SubElement(xrate_tc, "ntsc").text = NTSC
    ET.SubElement(xtimecode, "string").text = "00:00:00:00"
    ET.SubElement(xtimecode, "frame").text = "0"
    ET.SubElement(xtimecode, "displayformat").text = "NDF"

    xmedia = ET.SubElement(xsequence, "media")
    xvideo = ET.SubElement(xmedia, "video")
    xformat = ET.SubElement(xvideo, "format")
    xsample = ET.SubElement(xformat, "samplecharacteristics")
    ET.SubElement(xsample, "width").text = "3840"
    ET.SubElement(xsample, "height").text = "2160"
    ET.SubElement(xsample, "pixelaspectratio").text = "square"

    xtrack_v = ET.SubElement(xvideo, "track")

    curr_timeline_frame = 0

    for i, cut in enumerate(edl_cuts):
        seg_duration_sec = cut["end_sec"] - cut["start_sec"]
        seg_duration_frames = sec_to_frames(seg_duration_sec)

        src_in_frame = sec_to_frames(cut["start_sec"])
        src_out_frame = src_in_frame + seg_duration_frames

        tl_start_frame = curr_timeline_frame
        tl_end_frame = curr_timeline_frame + seg_duration_frames
        curr_timeline_frame = tl_end_frame

        cam_id = cut["cam"].upper()
        if cam_id == "CAM1":
            cam_path = cam1_path
            cam_label = "CAM1 (沈伯洋)"
            file_id = "masterclip-CAM1"
        else:
            cam_path = cam2_path
            cam_label = "CAM2 (工頭堅)"
            file_id = "masterclip-CAM2"

        is_reaction = "反應" in cut["reason"] or "反應" in cut["rule"]
        shot_label = f"[{'反應' if is_reaction else '主講'}] {cut['rule']}"

        xclip = ET.SubElement(xtrack_v, "clipitem", id=f"clipitem-v-{i+1:04d}")
        ET.SubElement(xclip, "name").text = f"Shot {i+1:03d} - {cam_label} - {shot_label}"
        ET.SubElement(xclip, "duration").text = str(seg_duration_frames)

        xrate_c = ET.SubElement(xclip, "rate")
        ET.SubElement(xrate_c, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_c, "ntsc").text = NTSC

        ET.SubElement(xclip, "start").text = str(tl_start_frame)
        ET.SubElement(xclip, "end").text = str(tl_end_frame)
        ET.SubElement(xclip, "in").text = str(src_in_frame)
        ET.SubElement(xclip, "out").text = str(src_out_frame)

        xfile = ET.SubElement(xclip, "file", id=file_id)
        ET.SubElement(xfile, "name").text = os.path.basename(cam_path)
        ET.SubElement(xfile, "pathurl").text = file_path_to_url(cam_path)
        xrate_f = ET.SubElement(xfile, "rate")
        ET.SubElement(xrate_f, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_f, "ntsc").text = NTSC
        ET.SubElement(xfile, "duration").text = "140000"

        xmedia_f = ET.SubElement(xfile, "media")
        xvideo_f = ET.SubElement(xmedia_f, "video")
        xsample_f = ET.SubElement(xvideo_f, "samplecharacteristics")
        ET.SubElement(xsample_f, "width").text = "3840"
        ET.SubElement(xsample_f, "height").text = "2160"

        xcomment = ET.SubElement(xclip, "comments")
        ET.SubElement(xcomment, "mastercomment1").text = f"規則: {cut['rule']} | 原因: {cut['reason']}"

        # DaVinci markers
        xmarker = ET.SubElement(xclip, "marker")
        ET.SubElement(xmarker, "name").text = f"C{i+1}: {cut['rule']}"
        ET.SubElement(xmarker, "comment").text = cut["reason"]
        ET.SubElement(xmarker, "color").text = "Blue" if is_reaction else "Orange"
        ET.SubElement(xmarker, "in").text = str(src_in_frame)
        ET.SubElement(xmarker, "out").text = str(src_out_frame)

    # Audio Media Setup: 6 Tracks
    xaudio = ET.SubElement(xmedia, "audio")

    # Calculate master audio offset:
    master_start_sec = global_start_sec - master_offset_sec
    master_in_frame = sec_to_frames(max(0.0, master_start_sec))
    master_out_frame = master_in_frame + total_duration_frames

    # A1 & A2: OBS Master Audio (WAV)
    for ch_idx, ch_name in [(1, "L"), (2, "R")]:
        xtrack_a = ET.SubElement(xaudio, "track")
        xclip_a = ET.SubElement(xtrack_a, "clipitem", id=f"audio-obs-master-{ch_name}")
        ET.SubElement(xclip_a, "name").text = f"Master Mix {ch_name} (OBS.mkv - 現場雙人混音)"
        ET.SubElement(xclip_a, "enabled").text = "TRUE"
        ET.SubElement(xclip_a, "duration").text = str(total_duration_frames)

        xrate_a = ET.SubElement(xclip_a, "rate")
        ET.SubElement(xrate_a, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_a, "ntsc").text = NTSC

        ET.SubElement(xclip_a, "start").text = "0"
        ET.SubElement(xclip_a, "end").text = str(total_duration_frames)
        ET.SubElement(xclip_a, "in").text = str(master_in_frame)
        ET.SubElement(xclip_a, "out").text = str(master_out_frame)

        xfile_a = ET.SubElement(xclip_a, "file", id="masterclip-OBS-wav")
        ET.SubElement(xfile_a, "name").text = os.path.basename(master_audio_path)
        ET.SubElement(xfile_a, "pathurl").text = file_path_to_url(master_audio_path)
        xrate_fa = ET.SubElement(xfile_a, "rate")
        ET.SubElement(xrate_fa, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_fa, "ntsc").text = NTSC
        ET.SubElement(xfile_a, "duration").text = "140000"

        xmedia_fa = ET.SubElement(xfile_a, "media")
        xaudio_fa = ET.SubElement(xmedia_fa, "audio")
        xsample_fa = ET.SubElement(xaudio_fa, "samplecharacteristics")
        ET.SubElement(xsample_fa, "depth").text = "16"
        ET.SubElement(xsample_fa, "samplerate").text = "48000"
        ET.SubElement(xaudio_fa, "channelcount").text = "2"

        xsrc_a = ET.SubElement(xclip_a, "sourcetrack")
        ET.SubElement(xsrc_a, "mediatype").text = "audio"
        ET.SubElement(xsrc_a, "trackindex").text = str(ch_idx)

    # A3 & A4: CAM1 Audio
    for ch_idx, ch_name in [(1, "L"), (2, "R")]:
        xtrack_a = ET.SubElement(xaudio, "track")
        xclip_a = ET.SubElement(xtrack_a, "clipitem", id=f"audio-cam1-{ch_name}")
        ET.SubElement(xclip_a, "name").text = f"CAM1 Audio {ch_name} (沈伯洋)"
        ET.SubElement(xclip_a, "enabled").text = "TRUE"
        ET.SubElement(xclip_a, "duration").text = str(total_duration_frames)

        xrate_a = ET.SubElement(xclip_a, "rate")
        ET.SubElement(xrate_a, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_a, "ntsc").text = NTSC

        ET.SubElement(xclip_a, "start").text = "0"
        ET.SubElement(xclip_a, "end").text = str(total_duration_frames)
        ET.SubElement(xclip_a, "in").text = str(global_start_frame)
        ET.SubElement(xclip_a, "out").text = str(global_end_frame)

        xfile_a = ET.SubElement(xclip_a, "file", id="masterclip-CAM1-audio")
        ET.SubElement(xfile_a, "name").text = os.path.basename(cam1_path)
        ET.SubElement(xfile_a, "pathurl").text = file_path_to_url(cam1_path)
        xrate_fa = ET.SubElement(xfile_a, "rate")
        ET.SubElement(xrate_fa, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_fa, "ntsc").text = NTSC
        ET.SubElement(xfile_a, "duration").text = "140000"

        xmedia_fa = ET.SubElement(xfile_a, "media")
        xaudio_fa = ET.SubElement(xmedia_fa, "audio")
        xsample_fa = ET.SubElement(xaudio_fa, "samplecharacteristics")
        ET.SubElement(xsample_fa, "depth").text = "16"
        ET.SubElement(xsample_fa, "samplerate").text = "48000"
        ET.SubElement(xaudio_fa, "channelcount").text = "2"

        xsrc_a = ET.SubElement(xclip_a, "sourcetrack")
        ET.SubElement(xsrc_a, "mediatype").text = "audio"
        ET.SubElement(xsrc_a, "trackindex").text = str(ch_idx)

    # A5 & A6: CAM2 Audio
    for ch_idx, ch_name in [(1, "L"), (2, "R")]:
        xtrack_a = ET.SubElement(xaudio, "track")
        xclip_a = ET.SubElement(xtrack_a, "clipitem", id=f"audio-cam2-{ch_name}")
        ET.SubElement(xclip_a, "name").text = f"CAM2 Audio {ch_name} (工頭堅)"
        ET.SubElement(xclip_a, "enabled").text = "TRUE"
        ET.SubElement(xclip_a, "duration").text = str(total_duration_frames)

        xrate_a = ET.SubElement(xclip_a, "rate")
        ET.SubElement(xrate_a, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_a, "ntsc").text = NTSC

        ET.SubElement(xclip_a, "start").text = "0"
        ET.SubElement(xclip_a, "end").text = str(total_duration_frames)
        ET.SubElement(xclip_a, "in").text = str(global_start_frame)
        ET.SubElement(xclip_a, "out").text = str(global_end_frame)

        xfile_a = ET.SubElement(xclip_a, "file", id="masterclip-CAM2-audio")
        ET.SubElement(xfile_a, "name").text = os.path.basename(cam2_path)
        ET.SubElement(xfile_a, "pathurl").text = file_path_to_url(cam2_path)
        xrate_fa = ET.SubElement(xfile_a, "rate")
        ET.SubElement(xrate_fa, "timebase").text = str(TIMEBASE)
        ET.SubElement(xrate_fa, "ntsc").text = NTSC
        ET.SubElement(xfile_a, "duration").text = "140000"

        xmedia_fa = ET.SubElement(xfile_a, "media")
        xaudio_fa = ET.SubElement(xmedia_fa, "audio")
        xsample_fa = ET.SubElement(xaudio_fa, "samplecharacteristics")
        ET.SubElement(xsample_fa, "depth").text = "16"
        ET.SubElement(xsample_fa, "samplerate").text = "48000"
        ET.SubElement(xaudio_fa, "channelcount").text = "2"

        xsrc_a = ET.SubElement(xclip_a, "sourcetrack")
        ET.SubElement(xsrc_a, "mediatype").text = "audio"
        ET.SubElement(xsrc_a, "trackindex").text = str(ch_idx)

    # Format pretty XML
    rough_xml = ET.tostring(xsequence, encoding="utf-8")
    reparsed = minidom.parseString(rough_xml)
    doctype_str = '<!DOCTYPE xmeml>\n<xmeml version="4">\n'
    pretty_body = reparsed.toprettyxml(indent="    ", encoding="utf-8").decode("utf-8")
    if pretty_body.startswith("<?xml"):
        pretty_body = pretty_body.split("?>\n", 1)[1]

    final_xml = doctype_str + pretty_body + "\n</xmeml>"

    with open(output_xml_path, "w", encoding="utf-8") as f:
        f.write(final_xml)

    print(f"✓ Successfully exported Test C XML: {output_xml_path}")

def main():
    edl_csv = "/Volumes/Crucial X10/muticam_test_file/output/edl_gemini_3.8_c.csv"
    cam1_path = "/Volumes/Crucial X10/muticam_test_file/output/CAM1_synced.mp4"
    cam2_path = "/Volumes/Crucial X10/muticam_test_file/output/CAM2_synced.mp4"
    obs_master_wav = "/Volumes/Crucial X10/muticam_test_file/output/OBS_master_audio.wav"
    
    offset_sec = 18.268

    output_clean_csv = "/Volumes/Crucial X10/muticam_test_file/output/edl_gemini_3.8_c_clean.csv"
    output_xml = "/Volumes/Crucial X10/muticam_test_file/output/final_cut_gemini_3.8_c_with_master_audio.xml"

    print("================================================================================")
    print("🚀 Exporting Test C: Gemini 3.8 Flash Pure Video Understanding XML")
    print("================================================================================")
    
    cleaned_edl = load_and_clean_c_edl(edl_csv)

    with open(output_clean_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Start_Time", "End_Time", "Best_Camera", "剪輯規則", "剪輯原因"])
        for c in cleaned_edl:
            def to_tc(s):
                m = int(s // 60)
                sec = s % 60
                return f"{m:02d}:{sec:06.3f}"
            writer.writerow([to_tc(c["start_sec"]), to_tc(c["end_sec"]), c["cam"], c["rule"], c["reason"]])
    print(f"✓ Saved Cleaned Test C CSV: {output_clean_csv} ({len(cleaned_edl)} cuts)")

    build_fcp7_xml_c(
        edl_cuts=cleaned_edl,
        cam1_path=cam1_path,
        cam2_path=cam2_path,
        master_audio_path=obs_master_wav,
        master_offset_sec=offset_sec,
        output_xml_path=output_xml
    )

if __name__ == "__main__":
    main()
