# dodobird1: prepare beatmap features but also able to use multiple threads for much better performance
import os
import sys
import sqlite3
import json
import subprocess
import traceback
import yaml
from tqdm import tqdm
import concurrent.futures
from multiprocessing import Manager

# Add project root to sys.path
sys.path.append(os.getcwd())
from mug.data.convertor import parse_osu_file
import minacalc

def invoke_osu_tools(beatmap_path, osu_tools, dotnet_path='dotnet'):
    try:
        cmd = [dotnet_path, osu_tools, "difficulty", beatmap_path, "-j"]
        result = json.loads(subprocess.check_output(cmd, stderr=subprocess.DEVNULL))
        return result['results'][0]['attributes']['star_rating']
    except Exception:
        return None

def process_single_beatmap(path, osu_tools, dotnet_path, ranked_maps):
    try:
        name = os.path.basename(path)
        set_name = os.path.basename(os.path.dirname(path))
        
        # Initial dictionary
        update_dict = {
            'name': name,
            'set_name': set_name
        }

        # 1. Parse OSU file
        ob, meta = parse_osu_file(path, None)
        
        # 2. Get Star Rating
        sr = invoke_osu_tools(path, osu_tools, dotnet_path)
        if sr is None: return None
        update_dict['sr'] = sr

        # 3. Rank Status
        update_dict["rank_status"] = ranked_maps.get(meta.set_id, "graveyard")

        # 4. LN Ratio
        ln, rc = 0, 0
        notes = []
        for l in ob:
            params = l.split(",")
            if len(params) < 5: continue
            start = int(float(params[2]))
            column = int(int(float(params[0])) / (512 / 4))
            column = min(max(column, 0), 3)
            notes.append((start, column))
            if int(params[3]) == 128: ln += 1
            else: rc += 1
            
        total = ln + rc
        if total == 0: return None
        ln_ratio = ln / total
        update_dict.update({
            'ln_ratio': ln_ratio,
            'rc': int(ln_ratio < 0.1),
            'ln': int(ln_ratio >= 0.4),
            'hb': int(0.1 <= ln_ratio <= 0.7)
        })

        # 5. Etterna / MinaCalc
        notes = sorted(notes, key=lambda x: x[0])
        res = minacalc.calc_skill_set(1.0, notes)
        keys = ["overall", "stream", "jumpstream", "handstream", "stamina", "jackspeed", "chordjack", "technical"]
        res_dict = dict(zip(keys, res))
        
        patterns = res_dict.copy()
        del patterns['overall']
        del patterns['stamina']
        max_score = max(patterns.values())

        update_dict.update({
            "ett": res_dict['overall'],
            "stream_ett": res_dict['stream'],
            "jumpstream_ett": res_dict['jumpstream'],
            "handstream_ett": res_dict['handstream'],
            "jackspeed_ett": res_dict['jackspeed'],
            "chordjack_ett": res_dict['chordjack'],
            "technical_ett": res_dict['technical'],
            "stamina_ett": res_dict['stamina'],
            "stream": int(max_score - res_dict['stream'] <= 1),
            "jumpstream": int(max_score - res_dict['jumpstream'] <= 1),
            "handstream": int(max_score - res_dict['handstream'] <= 1),
            "jackspeed": int(max_score - res_dict['jackspeed'] <= 1),
            "chordjack": int(max_score - res_dict['chordjack'] <= 1),
            "technical": int(max_score - res_dict['technical'] <= 1),
            "stamina": int(max_score - res_dict['stamina'] <= 1),
        })

        return update_dict
    except Exception:
        return None

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--beatmap_txt', '-b', type=str, required=True)
    parser.add_argument('--features_yaml', '-f', type=str, required=True)
    parser.add_argument('--osu_tools', type=str, required=True)
    parser.add_argument('--dotnet_path', type=str, default='dotnet')
    parser.add_argument('--workers', type=int, default=os.cpu_count() // 2)
    args = parser.parse_args()

    # Load ranked maps mapping if exists
    ranked_maps = {} 
    
    # Init DB
    db_path = os.path.join(os.path.dirname(args.beatmap_txt), 'feature.db')
    conn = sqlite3.connect(db_path)
    # Create table structure (simplified for speed, assuming standard columns)
    conn.execute("CREATE TABLE IF NOT EXISTS Feature (name TEXT, set_name TEXT, sr REAL, rank_status TEXT, ln_ratio REAL, rc INT, ln INT, hb INT, ett REAL, stream_ett REAL, jumpstream_ett REAL, handstream_ett REAL, jackspeed_ett REAL, chordjack_ett REAL, technical_ett REAL, stamina_ett REAL, stream INT, jumpstream INT, handstream INT, jackspeed INT, chordjack INT, technical INT, stamina INT, PRIMARY KEY (name, set_name))")
    conn.close()

    paths = [line.strip() for line in open(args.beatmap_txt, encoding='utf-8') if line.strip()]
    
    print(f"Starting parallel processing with {args.workers} workers...")
    results = []
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_single_beatmap, p, args.osu_tools, args.dotnet_path, ranked_maps): p for p in paths}
        
        for future in tqdm(concurrent.futures.as_completed(futures), total=len(futures)):
            res = future.result()
            if res:
                results.append(res)
                
            # Batch write to DB every 100 results
            if len(results) >= 100:
                conn = sqlite3.connect(db_path)
                columns = results[0].keys()
                query = f"INSERT OR REPLACE INTO Feature ({', '.join(columns)}) VALUES ({', '.join(['?']*len(columns))})"
                conn.executemany(query, [[r[c] for c in columns] for r in results])
                conn.commit()
                conn.close()
                results = []

    # Final write
    if results:
        conn = sqlite3.connect(db_path)
        columns = results[0].keys()
        query = f"INSERT OR REPLACE INTO Feature ({', '.join(columns)}) VALUES ({', '.join(['?']*len(columns))})"
        conn.executemany(query, [[r[c] for c in columns] for r in results])
        conn.commit()
        conn.close()

if __name__ == "__main__":
    main()
