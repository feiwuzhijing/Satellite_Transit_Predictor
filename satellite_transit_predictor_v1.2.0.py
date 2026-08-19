import flet as ft
import flet.canvas as flet_canvas
import flet_map
import asyncio
import numpy as np
import datetime
import math
import csv
import os
import sys
import requests
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
from skyfield.api import load, wgs84
from skyfield.framelib import itrs
import urllib3
import multiprocessing
from concurrent.futures import ProcessPoolExecutor

# 关闭由于本地根证书过期或系统时间不同步引发的 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 确保在 Android 等移动设备的私有数据目录下能够读写缓存文件
APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.environ['SKYFIELD_DATA'] = APP_DIR
os.chdir(APP_DIR)

tf = TimezoneFinder()
CST = datetime.timezone(datetime.timedelta(hours=8), name='CST')

SAT_SIZES = {
    'ISS (ZARYA)': 109.0, 'ISS': 109.0, 
    'CSS (TIANGONG)': 37.0, 'TIANHE': 16.6, 'WENTIAN': 17.9, 'MENGTIAN': 17.9,
    'HST': 13.2, 'HUBBLE': 13.2,
    'BLUEWALKER 3': 64.0, 'ENVISAT': 25.0, 'IRIDIUM': 30.0, 
    'LANDSAT': 15.0, 'SPOT': 10.0,
    'STARLINK': 7.0, 'STARLINK-V2': 30.0
}
DEFAULT_SAT_SIZE = 5.0

def get_bearing(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def get_destination(lat, lon, distance_km, bearing_deg):
    R = 6371.0
    lat1, lon1, brng = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    lat2 = math.asin(math.sin(lat1) * math.cos(distance_km / R) +
                     math.cos(lat1) * math.sin(distance_km / R) * math.cos(brng))
    lon2 = lon1 + math.atan2(math.sin(brng) * math.sin(distance_km / R) * math.cos(lat1),
                             math.cos(distance_km / R) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)

def get_utc_offset_str(dt):
    offset = dt.utcoffset()
    if offset is None: return "UTC+0"
    total_seconds = int(offset.total_seconds())
    hours = total_seconds // 3600
    minutes = (abs(total_seconds) % 3600) // 60
    sign = '+' if hours >= 0 else '-'
    if minutes == 0:
        return f"UTC{sign}{abs(hours)}"
    else:
        return f"UTC{sign}{abs(hours)}:{minutes:02d}"

_worker_calc = None
_worker_sats = None

def worker_init(cache_f, app_dir):
    global _worker_calc, _worker_sats
    import urllib3
    import os
    from skyfield.api import load
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    os.environ['SKYFIELD_DATA'] = app_dir
    os.chdir(app_dir)
    _worker_calc = TransitCalculator()
    _worker_sats = load.tle_file(cache_f)

def worker_compute_solar(sat_idx, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, focal_mm, pixel_um):
    global _worker_calc, _worker_sats
    sat = _worker_sats[sat_idx]
    results = _worker_calc.find_solar_transits(sat, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, focal_mm, pixel_um)
    for r in results:
        del r['sat_obj']
        r['sat_idx'] = sat_idx
    return results

def worker_compute_disc(sat_idx, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, target_name, focal_mm, pixel_um):
    global _worker_calc, _worker_sats
    sat = _worker_sats[sat_idx]
    results = _worker_calc.find_disc_transits(sat, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, target_name, focal_mm, pixel_um)
    for r in results:
        del r['sat_obj']
        r['sat_idx'] = sat_idx
    return results

class TransitCalculator:
    """完全保留的天体物理核心引擎，脱离了界面依赖"""
    def __init__(self):
        self.ts = load.timescale()
        self.eph = load('de421.bsp')
        self.earth = self.eph['earth']
        self.sun = self.eph['sun']
        self.moon = self.eph['moon']
        self.jupiter = self.eph['jupiter barycenter']
        self.saturn = self.eph['saturn barycenter']
        self.a = 6378.137
        self.b = 6356.752

    def haversine(self, lat1, lon1, lat2, lon2):
        R = 6371.0
        lat1_rad, lon1_rad = np.radians(lat1), np.radians(lon1)
        lat2_rad, lon2_rad = np.radians(lat2), np.radians(lon2)
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        a = np.sin(dlat/2)**2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon/2)**2
        return R * (2 * np.arctan2(np.sqrt(a), np.sqrt(1-a)))

    def get_satellite_size(self, sat):
        name_upper = sat.name.upper()
        if 'STARLINK' in name_upper:
            sat_num = sat.model.satnum if hasattr(sat, 'model') else 0
            if sat_num >= 55600: return 30.0 
            return 7.0 
        for known_sat, size in SAT_SIZES.items():
            if known_sat in name_upper: return size
        return DEFAULT_SAT_SIZE

    def fetch_tle_data(self, group_keys, force_download=False, backup_src='auto', custom_files=None):
        if custom_files is None: custom_files = {}
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36'}
        sources_map = {
            'celestrak_main': {
                'stations': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle"],
                'starlink': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle"],
                'oneweb': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle"],
                'visual': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=tle"],
                'active': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"]
            },
            'satnogs': {
                'stations': ["https://db.satnogs.org/api/tle/?group=stations"],
                'starlink': ["https://db.satnogs.org/api/tle/?group=starlink"]
            }
        }
        strategy_order = ['celestrak_main', 'satnogs']
        cache_dir = os.path.join(APP_DIR, "cache")
        os.makedirs(cache_dir, exist_ok=True)
        combined_text = ""

        def sanitize_tle(raw_text):
            return "\n".join([line.strip() for line in raw_text.splitlines() if line.strip()])

        for key in group_keys:
            if key in custom_files and custom_files[key] and os.path.exists(custom_files[key]):
                try:
                    with open(custom_files[key], "r", encoding="utf-8") as cf:
                        file_content = sanitize_tle(cf.read())
                        if len(file_content) > 50:
                            combined_text += "\n" + file_content
                            continue
                except: pass

            cache_file = os.path.join(cache_dir, f"tle_{key}.txt")
            if not force_download and os.path.exists(cache_file) and os.path.getsize(cache_file) > 100:
                try:
                    with open(cache_file, "r", encoding="utf-8") as cf:
                        content = sanitize_tle(cf.read())
                        if len(content) > 100 and '<html' not in content.lower():
                            combined_text += "\n" + content
                            continue
                except: pass

            success = False
            for src_name in strategy_order:
                if src_name not in sources_map or key not in sources_map[src_name]: continue
                urls = sources_map[src_name][key]
                for url in urls:
                    try:
                        resp = requests.get(url, headers=headers, timeout=10, verify=False)
                        if resp.status_code == 200 and len(resp.text) > 50:
                            text_data = sanitize_tle(resp.text)
                            if len(text_data) > 50 and '<html' not in text_data.lower():
                                combined_text += "\n" + text_data
                                with open(cache_file, "w", encoding="utf-8") as cf: cf.write(text_data)
                                success = True
                                break
                    except: pass
                if success: break

            if not success and os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as cf:
                        old_content = sanitize_tle(cf.read())
                        if len(old_content) > 0: combined_text += "\n" + old_content
                except: pass

        if len(combined_text.strip()) < 100:
            emergency_tle = """ISS (ZARYA)\n1 25544U 98067A   26065.50000000  .00016717  00000-0  30270-3 0  9999\n2 25544  51.6415 147.2882 0006249  88.5415 271.7456 15.50123853498142"""
            combined_text += "\n" + emergency_tle

        return combined_text

    def find_solar_transits(self, satellite, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, focal_mm, pixel_um):
        total_seconds = int((end_time - start_time).total_seconds())
        if total_seconds <= 0: return []
        step_coarse = 15 
        t_array_coarse = self.ts.utc(start_time.year, start_time.month, start_time.day, start_time.hour, start_time.minute, range(start_time.second, start_time.second + total_seconds, step_coarse))
        
        try: sat_pos_coarse = satellite.at(t_array_coarse).frame_xyz(itrs).km
        except: sat_pos_coarse = satellite.at(t_array_coarse).position.km
        sun_pos_coarse = self.earth.at(t_array_coarse).observe(self.sun).apparent().frame_xyz(itrs).km
        
        P, S = sat_pos_coarse, sun_pos_coarse
        D = P - S 
        norms = np.linalg.norm(D, axis=0)
        D = D / norms
        Px, Py, Pz = P[0], P[1], P[2]
        Dx, Dy, Dz = D[0], D[1], D[2]
        
        A = (Dx**2 + Dy**2)/self.a**2 + Dz**2/self.b**2
        B = 2 * ((Px*Dx + Py*Dy)/self.a**2 + Pz*Dz/self.b**2)
        C = (Px**2 + Py**2)/self.a**2 + Pz**2/self.b**2 - 1
        
        discriminant = B**2 - 4*A*C
        valid = discriminant >= 0
        if not np.any(valid): return []
        
        s = (-B[valid] - np.sqrt(discriminant[valid])) / (2 * A[valid])
        Ix, Iy, Iz = Px[valid] + s * Dx[valid], Py[valid] + s * Dy[valid], Pz[valid] + s * Dz[valid]
        
        Nx, Ny, Nz = Ix/self.a**2, Iy/self.a**2, Iz/self.b**2
        daytime = (Nx * S[0][valid] + Ny * S[1][valid] + Nz * S[2][valid]) > 0
        
        lat_rad = np.arctan2(Iz * self.a**2, np.sqrt(Ix**2 + Iy**2) * self.b**2)
        lon_rad = np.arctan2(Iy, Ix)
        lat, lon = np.degrees(lat_rad), np.degrees(lon_rad)
        
        valid_indices = np.where(valid)[0]
        daytime_indices = valid_indices[daytime]
        lat_day, lon_day = lat[daytime], lon[daytime]
        
        dist = self.haversine(lat_day, lon_day, obs_lat, obs_lon)
        broad_radius = max_radius_km + 400 if search_mode == 'regional' else 400
        hit_mask = dist < broad_radius
        hit_indices = daytime_indices[hit_mask]
        
        if len(hit_indices) == 0: return []
        events = []
        current_event = [hit_indices[0]]
        for i in range(1, len(hit_indices)):
            if hit_indices[i] - hit_indices[i-1] <= 3: current_event.append(hit_indices[i])
            else:
                events.append(current_event)
                current_event = [hit_indices[i]]
        events.append(current_event)
        
        results = []
        observer_loc = wgs84.latlon(obs_lat, obs_lon, elevation_m=obs_alt)
        observer = self.earth + observer_loc
        
        for event in events:
            idx_start = max(0, event[0] - 2)
            idx_end = min(len(t_array_coarse)-1, event[-1] + 2)
            t1 = t_array_coarse[idx_start].utc_datetime()
            t2 = t_array_coarse[idx_end].utc_datetime()
            
            fine_seconds = int((t2 - t1).total_seconds() * 5)
            if fine_seconds <= 0: continue
            
            t_array_fine = self.ts.utc(t1.year, t1.month, t1.day, t1.hour, t1.minute, np.linspace(t1.second, t1.second + (t2-t1).total_seconds(), fine_seconds))
            
            sun_f = self.earth.at(t_array_fine).observe(self.sun).apparent().frame_xyz(itrs).km
            sat_f = satellite.at(t_array_fine).frame_xyz(itrs).km
            D_f = sat_f - sun_f
            D_f = D_f / np.linalg.norm(D_f, axis=0)
            Px, Py, Pz, Dx, Dy, Dz = sat_f[0], sat_f[1], sat_f[2], D_f[0], D_f[1], D_f[2]
            
            A = (Dx**2 + Dy**2)/self.a**2 + Dz**2/self.b**2
            B = 2 * ((Px*Dx + Py*Dy)/self.a**2 + Pz*Dz/self.b**2)
            C = (Px**2 + Py**2)/self.a**2 + Pz**2/self.b**2 - 1
            disc = B**2 - 4*A*C
            v = disc >= 0
            if not np.any(v): continue
            
            s = (-B[v] - np.sqrt(disc[v])) / (2 * A[v])
            Ix, Iy, Iz = Px[v] + s * Dx[v], Py[v] + s * Dy[v], Pz[v] + s * Dz[v]
            lat_f = np.degrees(np.arctan2(Iz * self.a**2, np.sqrt(Ix**2 + Iy**2) * self.b**2))
            lon_f = np.degrees(np.arctan2(Iy, Ix))
            
            dist_f = self.haversine(lat_f, lon_f, obs_lat, obs_lon)
            min_dist_idx = np.argmin(dist_f)
            min_dist = dist_f[min_dist_idx]
            time_closest = t_array_fine[np.where(v)[0][min_dist_idx]]
            
            astrometric_sun = observer.at(time_closest).observe(self.sun).apparent()
            sun_alt, sun_az, _ = astrometric_sun.altaz()
            topocentric_sat = (satellite - observer_loc).at(time_closest)
            sat_dist_km = topocentric_sat.distance().km
            
            sun_angular_diam_rad = 0.0093 
            alt_rad = max(sun_alt.radians, 0.05)
            path_width_km = (sat_dist_km * sun_angular_diam_rad) / math.sin(alt_rad)
            
            if search_mode == 'exact':
                if min_dist > (path_width_km / 2.0): continue
            else:
                if min_dist > max_radius_km: continue
                
            sat_physical_size = self.get_satellite_size(satellite)
            covered_pixels = (sat_physical_size * focal_mm) / (sat_dist_km * pixel_um)
            
            path_coords = list(zip(lat_f, lon_f))
            left_band, right_band = [], []
            half_width = path_width_km / 2.0
            for i, (lat, lon) in enumerate(path_coords):
                bearing = get_bearing(lat, lon, path_coords[i+1][0], path_coords[i+1][1]) if i < len(path_coords) - 1 else 0.0
                left_band.append(get_destination(lat, lon, half_width, (bearing - 90) % 360))
                right_band.append(get_destination(lat, lon, half_width, (bearing + 90) % 360))
            
            utc_dt = time_closest.utc_datetime()
            try: local_tz = ZoneInfo(tf.timezone_at(lat=obs_lat, lng=obs_lon) or 'CST')
            except: local_tz = CST
            local_time = utc_dt.astimezone(local_tz)
            utc_off = get_utc_offset_str(local_time)
            
            ang_vel = (7.5 / sat_dist_km) * 206265 
            duration_sec = (0.53 * 3600) / ang_vel if ang_vel > 0 else 0
            start_utc = utc_dt - datetime.timedelta(seconds=duration_sec/2)
            end_utc = utc_dt + datetime.timedelta(seconds=duration_sec/2)
            
            results.append({
                'target_type': '太阳', 'sat_name': satellite.name,
                'norad_id': satellite.model.satnum if hasattr(satellite, 'model') else 'N/A',
                'closest_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'start_utc_str': start_utc.strftime('%Y-%m-%d %H:%M:%S'),
                'end_utc_str': end_utc.strftime('%Y-%m-%d %H:%M:%S'),
                'local_time_str': local_time.strftime('%Y-%m-%d %H:%M:%S'),
                'utc_offset_str': utc_off,
                'tz_name': str(local_tz),
                'duration': f"{duration_sec:.2f}",
                'min_sep': f"{(min_dist / sat_dist_km) * 3437.75:.2f}", 
                'alt': f"{sun_alt.degrees:.2f}", 'az': f"{sun_az.degrees:.2f}",
                'utc_dt': utc_dt, 'min_dist_center_km': min_dist, 'sat_dist_km': sat_dist_km,
                'path_width_km': path_width_km, 'covered_pixels': covered_pixels,
                'path_center': path_coords, 'path_left': left_band, 'path_right': right_band,
                'sat_obj': satellite
            })
        return results

    def find_disc_transits(self, satellite, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, target_name, focal_mm, pixel_um):
        target_objs = {'月球': {'obj': self.moon, 'radius_km': 1737.4}, '木星': {'obj': self.jupiter, 'radius_km': 69911.0}, '土星': {'obj': self.saturn, 'radius_km': 58232.0}}
        if target_name not in target_objs: return []
        t_info = target_objs[target_name]
        
        total_seconds = int((end_time - start_time).total_seconds())
        if total_seconds <= 0: return []
        t_array = self.ts.utc(start_time.year, start_time.month, start_time.day, start_time.hour, start_time.minute, range(start_time.second, start_time.second + total_seconds, 5))
        
        observer_loc = wgs84.latlon(obs_lat, obs_lon, elevation_m=obs_alt)
        observer = self.earth + observer_loc
        results = []
        in_transit = False
        
        for t in t_array:
            try:
                astrometric_target = observer.at(t).observe(t_info['obj']).apparent()
                alt, az, distance = astrometric_target.altaz()
                if alt.degrees < 5: continue
                topocentric_sat = (satellite - observer_loc).at(t)
                sat_dist_km = topocentric_sat.distance().km
                
                target_angular_radius_arcsec = (t_info['radius_km'] / distance.km) * 206265
                sat_physical_size = self.get_satellite_size(satellite)
                sat_angular_radius_arcsec = (sat_physical_size / sat_dist_km) * 206265 / 2.0
                sep_arcsec = astrometric_target.separation_from(topocentric_sat).arcseconds
                
                if sep_arcsec <= (target_angular_radius_arcsec + sat_angular_radius_arcsec + 10):
                    if not in_transit:
                        in_transit = True
                        utc_dt = t.utc_datetime()
                        try: local_tz = ZoneInfo(tf.timezone_at(lat=obs_lat, lng=obs_lon) or 'CST')
                        except: local_tz = CST
                        local_time = utc_dt.astimezone(local_tz)
                        utc_off = get_utc_offset_str(local_time)
                        
                        path_width_km = (target_angular_radius_arcsec * 2 / 206265) * sat_dist_km
                        lat_o, lon_o = obs_lat, obs_lon
                        path_coords = [(lat_o - 0.02, lon_o - 0.02), (lat_o + 0.02, lon_o + 0.02)]
                        
                        results.append({
                            'target_type': target_name, 'sat_name': satellite.name,
                            'norad_id': satellite.model.satnum if hasattr(satellite, 'model') else 'N/A',
                            'closest_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                            'start_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                            'end_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                            'local_time_str': local_time.strftime('%Y-%m-%d %H:%M:%S'),
                            'utc_offset_str': utc_off,
                            'tz_name': str(local_tz),
                            'duration': "~0.5",
                            'min_sep': f"{(sep_arcsec/60):.2f}", 'alt': f"{alt.degrees:.2f}", 'az': f"{az.degrees:.2f}",
                            'utc_dt': utc_dt, 'min_dist_center_km': 0.0, 'sat_dist_km': sat_dist_km,
                            'path_width_km': path_width_km, 'covered_pixels': (sat_physical_size * focal_mm) / (sat_dist_km * pixel_um),
                            'path_center': path_coords, 'path_left': path_coords, 'path_right': path_coords,
                            'sat_obj': satellite
                        })
                else: in_transit = False
            except: continue
        return results

class FletAppController:
    def __init__(self, page: ft.Page):
        self.page = page
        self.page.title = "卫星凌日/行星预测器"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.padding = 0

        self.calc = None
        self.transit_data_store = []
        self.stop_requested = False
        self.is_calculating = False
        self.custom_file_paths = {}
        self.selected_transit = None

        self.setup_ui()

    def setup_ui(self):
        # 参数面板 Controls
        self.lat_var = ft.TextField(label="纬度 (Lat)", value="39.9042", expand=True, dense=True)
        self.lon_var = ft.TextField(label="经度 (Lon)", value="116.4074", expand=True, dense=True)
        self.alt_var = ft.TextField(label="海拔 (m)", value="50.00", expand=True, dense=True)
        self.search_var = ft.TextField(label="地名搜索 (如: 北京)", expand=True, dense=True)
        
        self.focal_var = ft.TextField(label="主镜焦距 (mm)", value="480", expand=True, dense=True)
        self.pixel_var = ft.TextField(label="相机像元尺寸 (µm)", value="1.45", expand=True, dense=True)
        
        now = datetime.datetime.now(datetime.timezone.utc)
        self.start_var = ft.TextField(label="开始时间", value=now.strftime("%Y-%m-%d 00:00:00"), expand=True, dense=True)
        self.end_var = ft.TextField(label="结束时间", value=(now + datetime.timedelta(days=1)).strftime("%Y-%m-%d 23:59:59"), expand=True, dense=True)
        
        # 分组 Checkboxes
        self.chk_stations = ft.Checkbox(label="空间站 (ISS 等)", value=True)
        self.chk_sl_gen1 = ft.Checkbox(label="初代星链 (Gen 1)", value=False)
        self.chk_sl_gen2 = ft.Checkbox(label="大尺寸二代星链 (V2)", value=False)
        self.chk_oneweb = ft.Checkbox(label="OneWeb 网络", value=False)
        self.chk_visual = ft.Checkbox(label="肉眼可见亮星表", value=False)

        # 自定义星链 ID 范围
        self.chk_sl_range = ft.Checkbox(label="自定义星链 ID 范围", value=False)
        self.sl_min_id_var = ft.TextField(label="最小 NORAD ID", value="55600", expand=True, dense=True)
        self.sl_max_id_var = ft.TextField(label="最大 NORAD ID", value="99999", expand=True, dense=True)

        # 凌星目标
        self.target_sun = ft.Checkbox(label="太阳 (凌日)", value=True)
        self.target_moon = ft.Checkbox(label="月球 (凌月)", value=False)
        self.target_jupiter = ft.Checkbox(label="木星 (凌木)", value=False)
        self.target_saturn = ft.Checkbox(label="土星 (凌土)", value=False)

        self.mode_var = ft.RadioGroup(
            content=ft.Row([
                ft.Radio(value="exact", label="精确点搜索"),
                ft.Radio(value="regional", label="区域范围搜索")
            ]), value="exact"
        )
        self.rad_var = ft.TextField(label="搜索半径 (km)", value="50", expand=True, dense=True)
        self.max_sats_var = ft.TextField(label="每组最多颗数", value="300", expand=True, dense=True)

        self.theme_var = ft.Dropdown(
            label="主题配色",
            options=[
                ft.dropdown.Option("Dark", "专业暗色 (Dark)"),
                ft.dropdown.Option("AstroBlue", "深空蓝 (Astro Blue)"),
                ft.dropdown.Option("Light", "明亮 (Light)"),
                ft.dropdown.Option("Matrix", "黑客帝国 (Matrix)"),
            ],
            value="Dark",
            on_select=self.change_theme,
            expand=True,
            dense=True
        )
        self.map_style_var = ft.Dropdown(
            label="地图样式",
            options=[
                ft.dropdown.Option("osm", "标准路网 (OSM)"),
                ft.dropdown.Option("opentopo", "地形图 (OpenTopo)"),
                ft.dropdown.Option("esri", "卫星影像 (Esri)"),
                ft.dropdown.Option("cartodark", "暗夜深空 (Carto Dark)"),
                ft.dropdown.Option("cartolight", "极简明亮 (Carto Light)"),
            ],
            value="osm",
            on_select=self.change_map_style,
            expand=True,
            dense=True
        )
        self.force_download_var = ft.Checkbox(label="强制重新下载 TLE 轨道数据", value=False)

        # Map Widget 初始化
        self.map_widget = flet_map.Map(
            expand=True,
            initial_center=flet_map.MapLatitudeLongitude(39.9042, 116.4074),
            initial_zoom=9,
            on_long_press=self.on_map_long_press,
            on_secondary_tap=self.on_map_long_press,
            layers=[]
        )
        self.update_map_layers()

        # 结果与状态
        self.progress_bar = ft.ProgressBar(value=0, color=ft.Colors.RED, bgcolor=ft.Colors.GREY_900)
        self.spinner = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ft.Colors.BLUE_200, visible=False)
        self.status_var = ft.Text("就绪。请设定参数后点击计算。", color=ft.Colors.BLUE_200, expand=True)
        
        self.calc_btn = ft.Button("开始搜索", on_click=self.start_calculation, expand=True, style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_700))
        self.stop_btn = ft.Button("停止", on_click=self.stop_calculation, disabled=True, color=ft.Colors.RED)
        
        self.sort_var = ft.Dropdown(
            label="排序方式",
            options=[
                ft.dropdown.Option("time_asc", "默认 (时间先后)"),
                ft.dropdown.Option("px_desc", "预估像素从大到小"),
                ft.dropdown.Option("px_asc", "预估像素从小到大"),
                ft.dropdown.Option("dist_asc", "距离从小到大"),
                ft.dropdown.Option("center_asc", "偏离中心从小到大")
            ],
            value="time_asc",
            on_select=lambda e: self.render_results(),
            expand=True,
            dense=True
        )
        
        # 结果列表 (极简紧凑无大缝隙流布局)
        self.results_list = ft.ListView(expand=True, spacing=4, padding=0)

        # 响应式抽屉结构（移动端 Tab，宽屏左右并排）
        self.layout = ft.ResponsiveRow([
            ft.Column([
                ft.Tabs(
                    expand=True,
                    length=2,
                    content=ft.Column(
                        expand=True,
                        controls=[
                            ft.TabBar(
                                tabs=[
                                    ft.Tab(label="参数控制"),
                                    ft.Tab(label="结果列表"),
                                ]
                            ),
                            ft.TabBarView(
                                expand=True,
                                controls=[
                                    ft.ListView([
                                        ft.Row([self.search_var, ft.IconButton(ft.Icons.SEARCH, on_click=self.geocode_address)]),
                                        ft.Row([self.lat_var, self.lon_var, self.alt_var]),
                                        ft.Text("预测时间范围", weight="bold"),
                                        ft.Row([self.start_var, self.end_var]),
                                        ft.Text("目标设定", weight="bold"),
                                        ft.Row([self.target_sun, self.target_moon, self.target_jupiter, self.target_saturn], wrap=True),
                                        ft.Text("数据分组源", weight="bold"),
                                        ft.Row([self.chk_stations, self.chk_sl_gen1, self.chk_sl_gen2, self.chk_oneweb, self.chk_visual], wrap=True),
                                        ft.Row([self.chk_sl_range], wrap=True),
                                        ft.Row([self.sl_min_id_var, self.sl_max_id_var]),
                                        self.force_download_var,
                                        self.mode_var,
                                        ft.Row([self.rad_var, self.max_sats_var]),
                                        ft.Row([self.focal_var, self.pixel_var]),
                                        ft.Row([self.theme_var, self.map_style_var]),
                                        ft.Row([self.calc_btn, self.stop_btn]),
                                        self.progress_bar,
                                        ft.Row([self.spinner, self.status_var])
                                    ], padding=15, spacing=10, expand=True),
                                    ft.Container(
                                        padding=ft.Padding.only(top=8, left=6, right=6, bottom=2),
                                        content=ft.Column([
                                            ft.Row([
                                                self.sort_var,
                                                ft.Button(content="CSV", icon=ft.Icons.DOWNLOAD, on_click=self.export_csv, height=34, style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8))),
                                                ft.Button(content="iCal", icon=ft.Icons.CALENDAR_MONTH, on_click=self.export_ical, height=34, style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8))),
                                            ], spacing=6),
                                            ft.Container(content=self.results_list, expand=True, padding=0)
                                        ], spacing=6, expand=True)
                                    )
                                ]
                            )
                        ]
                    )
                )
            ], col={"sm": 12, "md": 5, "lg": 4}, expand=True),
            
            ft.Column([
                ft.Container(content=self.map_widget, expand=True, border_radius=10, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
            ], col={"sm": 12, "md": 7, "lg": 8}, expand=True)
        ], expand=True)

        # File Pickers for Exports (Removed to ensure UI loads correctly)
        # self.csv_picker = ft.FilePicker()
        # self.ical_picker = ft.FilePicker()
        # self.page.overlay.extend([self.csv_picker, self.ical_picker])

    def change_theme(self, e):
        val = self.theme_var.value
        if val == "Dark":
            self.page.theme_mode = ft.ThemeMode.DARK
            self.page.bgcolor = "#121212"
            self.page.theme = None
        elif val == "AstroBlue":
            self.page.theme_mode = ft.ThemeMode.DARK
            self.page.bgcolor = "#0b132b"
            self.page.theme = ft.Theme(color_scheme_seed=ft.Colors.INDIGO)
        elif val == "Light":
            self.page.theme_mode = ft.ThemeMode.LIGHT
            self.page.bgcolor = "#f5f5f5"
            self.page.theme = None
        elif val == "Matrix":
            self.page.theme_mode = ft.ThemeMode.DARK
            self.page.bgcolor = "#000000"
            self.page.theme = ft.Theme(color_scheme_seed=ft.Colors.GREEN)
        self.render_results()
        self.page.update()

    def change_map_style(self, e):
        self.update_map_layers()

    def update_map_layers(self):
        self.map_widget.layers.clear()
        
        style = getattr(self, 'map_style_var', None)
        style_val = style.value if style else "osm"
        if style_val == "osm":
            url = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
        elif style_val == "opentopo":
            url = "https://a.tile.opentopomap.org/{z}/{x}/{y}.png"
        elif style_val == "esri":
            url = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        elif style_val == "cartodark":
            url = "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png"
        elif style_val == "cartolight":
            url = "https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png"
        else:
            url = "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png"
            
        self.map_widget.layers.append(
            flet_map.TileLayer(url_template=url, user_agent_package_name="transit_app")
        )
        # Observer Mark
        try: lat, lon = float(self.lat_var.value), float(self.lon_var.value)
        except: lat, lon = 39.9, 116.4
        
        self.map_widget.layers.append(
            flet_map.MarkerLayer(
                markers=[flet_map.Marker(coordinates=flet_map.MapLatitudeLongitude(lat, lon), content=ft.Icon(ft.Icons.LOCATION_ON, color="red", size=40))]
            )
        )
        
        # Polyline Corridors
        if self.selected_transit:
            t = self.selected_transit
            polys = []
            if t['path_center']:
                polys.append(flet_map.PolylineMarker(coordinates=[flet_map.MapLatitudeLongitude(p[0], p[1]) for p in t['path_center']], color=ft.Colors.RED, stroke_width=3))
            if t['path_left'] and t['path_right']:
                polys.append(flet_map.PolylineMarker(coordinates=[flet_map.MapLatitudeLongitude(p[0], p[1]) for p in t['path_left']], color=ft.Colors.GREY_500, stroke_width=2))
                polys.append(flet_map.PolylineMarker(coordinates=[flet_map.MapLatitudeLongitude(p[0], p[1]) for p in t['path_right']], color=ft.Colors.GREY_500, stroke_width=2))
            self.map_widget.layers.append(flet_map.PolylineLayer(polylines=polys))
        self.page.update()

    async def geocode_address(self, e):
        address = self.search_var.value.strip()
        if not address: return
        self.safe_status_update(f"正在智能搜索地点: {address}...")
        try:
            url = f"https://nominatim.openstreetmap.org/search?q={address}&format=json&limit=1"
            resp = await asyncio.to_thread(requests.get, url, headers={'User-Agent': 'Flet_App'}, timeout=5)
            if resp.status_code == 200 and len(resp.json()) > 0:
                data = resp.json()[0]
                lat, lon = float(data['lat']), float(data['lon'])
                self.lat_var.value, self.lon_var.value = f"{lat:.6f}", f"{lon:.6f}"
                self.update_map_layers()
                self.page.run_task(self.fetch_elevation_async, lat, lon)
        except: self.safe_status_update("地点搜索失败，请检查网络。")

    def on_map_long_press(self, e):
        lat, lon = e.coordinates.latitude, e.coordinates.longitude
        self.lat_var.value, self.lon_var.value = f"{lat:.6f}", f"{lon:.6f}"
        self.safe_status_update(f"已更新观测点: {lat:.4f}, {lon:.4f}。正在获取海拔...")
        self.update_map_layers()
        self.page.run_task(self.fetch_elevation_async, lat, lon)

    async def fetch_elevation_async(self, lat, lon):
        try:
            url = f"https://api.open-meteo.com/v1/elevation?latitude={lat}&longitude={lon}"
            resp = await asyncio.to_thread(requests.get, url, timeout=5)
            if resp.status_code == 200 and resp.json().get("elevation"):
                self.alt_var.value = f"{float(resp.json()['elevation'][0]):.2f}"
                self.safe_status_update(f"已更新观测点海拔: {self.alt_var.value}m")
        except: self.safe_status_update("海拔获取失败，保持原值。")
        self.page.update()

    def safe_status_update(self, msg, progress=None):
        self.status_var.value = msg
        if progress is not None: self.progress_bar.value = progress / 100.0
        self.page.update()

    def stop_calculation(self, e):
        self.stop_requested = True
        self.safe_status_update("正在请求停止计算...")

    def start_calculation(self, e):
        try:
            lat, lon, alt = float(self.lat_var.value), float(self.lon_var.value), float(self.alt_var.value)
            mode = self.mode_var.value
            rad = float(self.rad_var.value)
            foc_mm = float(self.focal_var.value)
            pix_um = float(self.pixel_var.value)
            max_sats = int(self.max_sats_var.value)
            
            fmt = "%Y-%m-%d %H:%M:%S" if ":" in self.start_var.value else "%Y-%m-%d"
            start = datetime.datetime.strptime(self.start_var.value.strip(), fmt).replace(tzinfo=datetime.timezone.utc)
            end = datetime.datetime.strptime(self.end_var.value.strip(), fmt).replace(tzinfo=datetime.timezone.utc)
            
            groups = []
            if self.chk_stations.value: groups.append('stations')
            if self.chk_sl_gen1.value or self.chk_sl_gen2.value or self.chk_sl_range.value: groups.append('starlink')
            if self.chk_oneweb.value: groups.append('oneweb')
            if self.chk_visual.value: groups.append('visual')
            
            targets = []
            if self.target_sun.value: targets.append('太阳')
            if self.target_moon.value: targets.append('月球')
            if self.target_jupiter.value: targets.append('木星')
            if self.target_saturn.value: targets.append('土星')
            
            if not targets: return self.safe_status_update("错误：请至少勾选一个凌星目标！")
            if not groups: return self.safe_status_update("错误：未选择预设分组！")
            
        except Exception as err:
            return self.safe_status_update(f"参数错误：{err}")

        self.calc_btn.disabled = True
        self.stop_btn.disabled = False
        self.stop_requested = False
        self.is_calculating = True
        self.spinner.visible = True
        self.results_list.controls.clear()
        self.transit_data_store.clear()
        self.update_map_layers()
        
        # 挂载后台异步测算任务 (防止阻塞 Android UI)
        force_dl = self.force_download_var.value
        self.page.run_task(self.run_calc_async, lat, lon, alt, mode, rad, foc_mm, pix_um, start, end, groups, max_sats, targets, force_dl)

    async def run_calc_async(self, lat, lon, alt, mode, rad, foc_mm, pix_um, start, end, groups, max_sats, targets, force_dl):
        try:
            if self.calc is None:
                self.safe_status_update("初始化天体物理核心...", 5)
                self.calc = await asyncio.to_thread(TransitCalculator)

            self.safe_status_update("正在通过免封锁API拉取轨道数据...", 10)
            def fetch(): return self.calc.fetch_tle_data(groups, force_download=force_dl)
            tle_text = await asyncio.to_thread(fetch)
            
            cache_f = os.path.join(APP_DIR, "cache", "combined_tle.txt")
            if len(tle_text.strip()) > 0:
                with open(cache_f, "w", encoding="utf-8") as cf: cf.write(tle_text.strip() + "\n")
            
            self.safe_status_update("正在本地解析 TLE...", 15)
            satellites = await asyncio.to_thread(load.tle_file, cache_f)
            
            # 本地过滤
            filtered_indices = []
            cat_counts = {'sl1': 0, 'sl2': 0, 'sl_range': 0, 'oneweb': 0, 'stations': 0, 'other': 0}
            
            sl_range_enabled = self.chk_sl_range.value
            try: sl_min_id = int(self.sl_min_id_var.value.strip())
            except: sl_min_id = 0
            try: sl_max_id = int(self.sl_max_id_var.value.strip())
            except: sl_max_id = 999999

            for i in range(len(satellites) - 1, -1, -1):
                sat = satellites[i]
                name = sat.name.upper()
                sat_id = sat.model.satnum if hasattr(sat, 'model') else 0
                if 'STARLINK' in name:
                    if sl_range_enabled:
                        if sl_min_id <= sat_id <= sl_max_id:
                            if cat_counts['sl_range'] < max_sats: cat_counts['sl_range'] += 1; filtered_indices.append(i)
                    else:
                        if sat_id >= 55600 and self.chk_sl_gen2.value:
                            if cat_counts['sl2'] < max_sats: cat_counts['sl2'] += 1; filtered_indices.append(i)
                        elif sat_id < 55600 and self.chk_sl_gen1.value:
                            if cat_counts['sl1'] < max_sats: cat_counts['sl1'] += 1; filtered_indices.append(i)
                elif 'ONEWEB' in name and self.chk_oneweb.value:
                    if cat_counts['oneweb'] < max_sats: cat_counts['oneweb'] += 1; filtered_indices.append(i)
                elif any(x in name for x in ['ISS', 'ZARYA', 'CSS', 'TIANGONG']) and self.chk_stations.value:
                    if cat_counts['stations'] < max_sats: cat_counts['stations'] += 1; filtered_indices.append(i)
                elif self.chk_visual.value:
                    if cat_counts['other'] < max_sats: cat_counts['other'] += 1; filtered_indices.append(i)
            
            tot = len(filtered_indices)
            if tot == 0: return self.safe_status_update("未找到符合条件的卫星。", 100)
            
            self.safe_status_update(f"过滤出 {tot} 颗目标卫星，启动多进程高精度轨道演化...", 20)
            all_transits = []
            
            loop = asyncio.get_running_loop()
            executor = ProcessPoolExecutor(initializer=worker_init, initargs=(cache_f, APP_DIR))
            
            try:
                futures = []
                for sat_idx in filtered_indices:
                    for tg in targets:
                        if tg == '太阳':
                            futures.append(loop.run_in_executor(executor, worker_compute_solar, sat_idx, start, end, lat, lon, alt, mode, rad, foc_mm, pix_um))
                        else:
                            futures.append(loop.run_in_executor(executor, worker_compute_disc, sat_idx, start, end, lat, lon, alt, mode, rad, tg, foc_mm, pix_um))
                
                total_tasks = len(futures)
                completed_count = 0
                
                for coro in asyncio.as_completed(futures):
                    if self.stop_requested:
                        break
                    
                    try:
                        events = await coro
                        for e in events:
                            e['sat_obj'] = satellites[e['sat_idx']]
                        all_transits.extend(events)
                    except Exception as ex:
                        print(f"Worker calculation error: {ex}")
                    
                    completed_count += 1
                    progress = 20 + (completed_count / total_tasks) * 75
                    self.safe_status_update(f"物理测算中 [{completed_count}/{total_tasks}]", progress)
                    
            finally:
                if sys.version_info >= (3, 9):
                    executor.shutdown(wait=False, cancel_futures=True)
                else:
                    executor.shutdown(wait=False)

            msg = f"计算中断！找到 {len(all_transits)} 次凌星。" if self.stop_requested else f"完毕！共找到 {len(all_transits)} 次凌星事件。"
            self.safe_status_update(msg, 100)
            self.transit_data_store = all_transits
            self.render_results()

        except Exception as e:
            self.safe_status_update(f"计算异常: {e}", 0)
        finally:
            self.spinner.visible = False
            self.calc_btn.disabled = False
            self.stop_btn.disabled = True
            self.is_calculating = False
            self.page.update()

    def render_results(self):
        self.results_list.controls.clear()
        
        theme_val = getattr(self.theme_var, 'value', 'Dark')
        if theme_val == "AstroBlue":
            card_bg = "#161b22"
            border_c = "#30363d"
            tag_bg = "#1f6feb"
            tag_text_c = ft.Colors.WHITE
            title_c = "#58a6ff"
            text_c = "#c9d1d9"
            sub_text_c = ft.Colors.GREY_500
            accent_c = "#79c0ff"
            btn_style = ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8))
        elif theme_val == "Matrix":
            card_bg = "#001100"
            border_c = "#005500"
            tag_bg = "#003300"
            tag_text_c = "#39ff14"
            title_c = "#39ff14"
            text_c = "#00ff00"
            sub_text_c = "#00aa00"
            accent_c = "#39ff14"
            btn_style = ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8), color="#39ff14")
        elif theme_val == "Light":
            card_bg = "#ffffff"
            border_c = "#d0d7de"
            tag_bg = "#cf222e"
            tag_text_c = ft.Colors.WHITE
            title_c = "#0969da"
            text_c = "#24292f"
            sub_text_c = ft.Colors.GREY_600
            accent_c = "#bf8700"
            btn_style = ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8))
        else: # Dark
            card_bg = "#1c1c1c"
            border_c = "#333333"
            tag_bg = "#9e1523"
            tag_text_c = ft.Colors.WHITE
            title_c = "#61afef"
            text_c = "#abb2bf"
            sub_text_c = ft.Colors.GREY_500
            accent_c = "#e5c07b"
            btn_style = ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=8))

        sort_val = self.sort_var.value if hasattr(self, 'sort_var') else "time_asc"
        if sort_val == "px_desc":
            sorted_data = sorted(self.transit_data_store, key=lambda x: float(x['covered_pixels']), reverse=True)
        elif sort_val == "px_asc":
            sorted_data = sorted(self.transit_data_store, key=lambda x: float(x['covered_pixels']))
        elif sort_val == "dist_asc":
            sorted_data = sorted(self.transit_data_store, key=lambda x: float(x['sat_dist_km']))
        elif sort_val == "center_asc":
            sorted_data = sorted(self.transit_data_store, key=lambda x: float(x['min_dist_center_km']))
        else:
            sorted_data = sorted(self.transit_data_store, key=lambda x: x['utc_dt'])

        for t in sorted_data:
            item_box = ft.Container(
                bgcolor=card_bg,
                border=ft.Border.all(1, border_c),
                border_radius=6,
                padding=8,
                margin=ft.Margin.only(bottom=2),
                content=ft.Column([
                    ft.Row([
                        ft.Text(f"{t['sat_name']} ({t['norad_id']})", size=13, weight="bold", color=title_c),
                        ft.Container(content=ft.Text(t['target_type'], size=10, color=tag_text_c, weight="bold"), bgcolor=tag_bg, padding=ft.Padding.symmetric(horizontal=6, vertical=2), border_radius=4)
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                    
                    ft.Row([
                        ft.Column([
                            ft.Text(f"接近(本地): {t.get('local_time_str', 'N/A')} ({t.get('utc_offset_str', 'UTC+8')})", size=12, weight="bold", color=title_c),
                            ft.Text(f"最接近(UTC): {t['closest_utc_str']}", size=11, color=text_c),
                            ft.Text(f"时区: {t.get('tz_name', 'CST')} | 开始/结束(UTC): {t.get('start_utc_str', 'N/A')[-8:]}-{t.get('end_utc_str', 'N/A')[-8:]}", size=10, color=sub_text_c),
                        ], expand=True, spacing=1),
                        
                        ft.Column([
                            ft.Text(f"预估像素: {t['covered_pixels']:.1f} px", size=12, weight="bold", color=accent_c),
                            ft.Text(f"偏离中心: {t['min_dist_center_km']:.2f} km", size=11, color=text_c),
                            ft.Text(f"距离: {t['sat_dist_km']:.1f} km | 带宽: {t['path_width_km']:.2f} km", size=10, color=text_c),
                            ft.Text(f"高度角: {t['alt']}° | 方位: {t['az']}° | 持续: {t['duration']}s", size=10, color=sub_text_c),
                        ], expand=True, spacing=1),
                    ], spacing=10),
                    
                    ft.Row([
                        ft.Button(content="高亮走廊", icon=ft.Icons.MAP, on_click=lambda e, data=t: self.focus_map_on(data), height=26, style=btn_style),
                        ft.Button(content="视角模拟", icon=ft.Icons.REMOVE_RED_EYE, on_click=lambda e, data=t: self.show_simulation(data), height=26, style=btn_style)
                    ], alignment=ft.MainAxisAlignment.END, spacing=8)
                ], spacing=4)
            )
            self.results_list.controls.append(item_box)
        self.page.update()

    def focus_map_on(self, t):
        self.selected_transit = t
        if t['path_center']:
            mid = t['path_center'][len(t['path_center']) // 2]
            # Android/iOS 使用 Flet-map 可重新渲染 Layer 来聚焦
            self.update_map_layers()

    def show_simulation(self, data):
        """视角模拟弹窗：在选定地点看向天体，带飞行方向箭头"""
        cv = flet_canvas.Canvas(width=320, height=340)
        
        # 1. 瞬间预渲染基础天体与十字丝，保证背景100%秒级可靠加载
        cx, cy = 160, 160
        r_px = 105
        target_type = data['target_type']
        color_map = {'太阳': ft.Colors.YELLOW_600, '月球': ft.Colors.WHITE, '木星': ft.Colors.ORANGE_300, '土星': ft.Colors.AMBER_100}
        
        cv.shapes.append(flet_canvas.Circle(cx, cy, r_px, ft.Paint(color=color_map.get(target_type, ft.Colors.GREY), style=ft.PaintingStyle.FILL)))
        cv.shapes.append(flet_canvas.Line(cx, cy - r_px - 15, cx, cy + r_px + 15, ft.Paint(color=ft.Colors.GREY_800, stroke_width=1)))
        cv.shapes.append(flet_canvas.Line(cx - r_px - 15, cy, cx + r_px + 15, cy, ft.Paint(color=ft.Colors.GREY_800, stroke_width=1)))
        cv.shapes.append(flet_canvas.Text(cx - 24, cy - r_px - 30, "天顶 (Up)", style=ft.TextStyle(color=ft.Colors.BLUE_200, size=11, weight="bold")))
        cv.shapes.append(flet_canvas.Text(cx - 24, cy + r_px + 12, "地底 (Down)", style=ft.TextStyle(color=ft.Colors.GREY_600, size=10)))

        sheet_content = ft.Container(
            padding=15,
            content=ft.Column([
                ft.Text(f"视角模拟: {data['target_type']} - {data['sat_name']}", size=16, weight="bold"),
                ft.Text("视角说明: 在选定地点仰望天体 (天顶朝上，红色箭头指示卫星运动方向)", size=11, color=ft.Colors.GREY_400),
                ft.Container(content=cv, bgcolor=ft.Colors.BLACK, border_radius=10, width=320, height=340)
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8)
        )
        bs = ft.BottomSheet(sheet_content, open=True)
        self.page.overlay.append(bs)
        self.page.update()
        
        # 2. 挂载渲染轨迹与强力矢量的异步任务
        self.page.run_task(self.render_sim_canvas, cv, data)

    async def render_sim_canvas(self, cv, data):
        sat = data['sat_obj']
        target_type = data['target_type']
        utc_dt = data['utc_dt']
        lat, lon, alt = float(self.lat_var.value), float(self.lon_var.value), float(self.alt_var.value)
        
        def compute_sim_points():
            ts = self.calc.ts
            observer_loc = wgs84.latlon(lat, lon, elevation_m=alt)
            observer = self.calc.earth + observer_loc
            target_obj = self.calc.sun if target_type == '太阳' else self.calc.moon if target_type == '月球' else None
            if target_type == '木星': target_obj = self.calc.jupiter
            if target_type == '土星': target_obj = self.calc.saturn
            if not target_obj: return None, []
            
            # 延长采样秒数，使得穿盘轨迹更完整长远
            pad = 2.0
            t_start = utc_dt - datetime.timedelta(seconds=pad)
            t_end = utc_dt + datetime.timedelta(seconds=pad)
            t0 = ts.utc(t_start.year, t_start.month, t_start.day, t_start.hour, t_start.minute, t_start.second + t_start.microsecond/1e6)
            t_mid = ts.utc(utc_dt.year, utc_dt.month, utc_dt.day, utc_dt.hour, utc_dt.minute, utc_dt.second + utc_dt.microsecond/1e6)
            t1 = ts.utc(t_end.year, t_end.month, t_end.day, t_end.hour, t_end.minute, t_end.second + t_end.microsecond/1e6)
            
            points = []
            target_radius_deg = 0.26
            for t in [t0, t_mid, t1]:
                astro_target = observer.at(t).observe(target_obj).apparent()
                target_alt, target_az, target_dist = astro_target.altaz()
                topocentric_sat = (sat - observer_loc).at(t)
                sat_alt, sat_az, _ = topocentric_sat.altaz()
                
                dx_deg = ((sat_az.degrees - target_az.degrees + 180) % 360 - 180) * np.cos(np.radians(target_alt.degrees))
                dy_deg = sat_alt.degrees - target_alt.degrees
                points.append((dx_deg, dy_deg))
                
                if t == t_mid:
                    if target_type == '太阳': target_radius_deg = 0.266
                    elif target_type == '月球': target_radius_deg = 0.25 
                    elif target_type == '木星': target_radius_deg = (69911.0 / target_dist.km) * (180/math.pi)
                    elif target_type == '土星': target_radius_deg = (58232.0 / target_dist.km) * (180/math.pi)
            return target_radius_deg, points
            
        target_radius_deg, points = await asyncio.to_thread(compute_sim_points)
        if not points: return
        
        cx, cy = 160, 160
        r_px = 105
        scale = r_px / target_radius_deg if target_radius_deg > 0 else r_px / 0.26
        
        # 轨迹与飞行方向箭头叠加
        if len(points) >= 3:
            px1, py1 = cx + points[0][0] * scale, cy - points[0][1] * scale
            px2, py2 = cx + points[1][0] * scale, cy - points[1][1] * scale
            px3, py3 = cx + points[2][0] * scale, cy - points[2][1] * scale
            
            # 1. 绘制红色主轨迹线
            cv.shapes.append(flet_canvas.Path(
                [flet_canvas.Path.MoveTo(px1, py1), flet_canvas.Path.LineTo(px2, py2), flet_canvas.Path.LineTo(px3, py3)],
                paint=ft.Paint(color=ft.Colors.RED, stroke_width=3, style=ft.PaintingStyle.STROKE)
            ))
            
            # 2. 在最接近中心点画绿色高亮标志
            cv.shapes.append(flet_canvas.Circle(px2, py2, 4, ft.Paint(color=ft.Colors.GREEN)))
            
            # 3. 终点粗艳红色方向箭头 (线条翼 + 多边形双重保障渲染)
            angle = math.atan2(py3 - py2, px3 - px2)
            head_len = 16
            head_angle = math.radians(25)
            
            a1_x = px3 - head_len * math.cos(angle - head_angle)
            a1_y = py3 - head_len * math.sin(angle - head_angle)
            a2_x = px3 - head_len * math.cos(angle + head_angle)
            a2_y = py3 - head_len * math.sin(angle + head_angle)
            
            # 双重加固翼线
            cv.shapes.append(flet_canvas.Line(px3, py3, a1_x, a1_y, ft.Paint(color=ft.Colors.RED, stroke_width=3, stroke_cap=ft.StrokeCap.ROUND)))
            cv.shapes.append(flet_canvas.Line(px3, py3, a2_x, a2_y, ft.Paint(color=ft.Colors.RED, stroke_width=3, stroke_cap=ft.StrokeCap.ROUND)))
            cv.shapes.append(flet_canvas.Line(a1_x, a1_y, a2_x, a2_y, ft.Paint(color=ft.Colors.RED, stroke_width=2, stroke_cap=ft.StrokeCap.ROUND)))
            
            cv.shapes.append(flet_canvas.Path(
                [
                    flet_canvas.Path.MoveTo(px3, py3),
                    flet_canvas.Path.LineTo(a1_x, a1_y),
                    flet_canvas.Path.LineTo(a2_x, a2_y),
                    flet_canvas.Path.Close()
                ],
                paint=ft.Paint(color=ft.Colors.RED, style=ft.PaintingStyle.FILL)
            ))
            
        cv.update()

    async def export_csv(self, e):
        if not self.transit_data_store: return self.safe_status_update("没有可导出的数据！")
        
        # 兼容手机与桌面的默认导出路径
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(downloads_dir): downloads_dir = APP_DIR
        path = os.path.join(downloads_dir, f"transit_results_{int(datetime.datetime.now().timestamp())}.csv")
        
        try:
            with open(path, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["卫星/航天器", "NORAD ID", "目标", "接近(本地时间)", "本地时区", "最接近(UTC)", "持续(秒)", "最小角距(角分)", "偏离中心(km)", "像素预估(px)"])
                for t in self.transit_data_store:
                    writer.writerow([t['sat_name'], t['norad_id'], t['target_type'], t.get('local_time_str', 'N/A'), t.get('tz_name', 'CST'), t['closest_utc_str'], t['duration'], t['min_sep'], f"{t['min_dist_center_km']:.2f}", f"{t['covered_pixels']:.1f}"])
            self.safe_status_update(f"已导出至: {path}")
            self.page.overlay.append(ft.SnackBar(ft.Text(f"成功导出: {path}"), open=True))
            self.page.update()
        except Exception as ex:
            self.safe_status_update(f"导出失败: {ex}")

    async def export_ical(self, e):
        if not self.transit_data_store: return self.safe_status_update("没有可导出的数据！")
        
        downloads_dir = os.path.join(os.path.expanduser("~"), "Downloads")
        if not os.path.exists(downloads_dir): downloads_dir = APP_DIR
        path = os.path.join(downloads_dir, f"transit_events_{int(datetime.datetime.now().timestamp())}.ics")
        
        try:
            lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Transit Predictor//CN"]
            for t in self.transit_data_store:
                utc_dt = t['utc_dt']
                start_str = utc_dt.strftime("%Y%m%dT%H%M%SZ")
                end_str = (utc_dt + datetime.timedelta(seconds=5)).strftime("%Y%m%dT%H%M%SZ")
                lines.extend([
                    "BEGIN:VEVENT", f"UID:transit-{start_str}-{t['sat_name']}@predictor",
                    f"DTSTAMP:{start_str}", f"DTSTART:{start_str}", f"DTEND:{end_str}",
                    f"SUMMARY:凌星预测: {t['sat_name']} ({t['target_type']})",
                    f"DESCRIPTION:预估大小: {t['covered_pixels']:.1f}px, 偏离中心: {t['min_dist_center_km']:.2f}km",
                    "END:VEVENT"
                ])
            lines.append("END:VCALENDAR")
            with open(path, 'w', encoding='utf-8') as f: f.write("\r\n".join(lines))
            self.safe_status_update(f"已导出日历至: {path}")
            self.page.overlay.append(ft.SnackBar(ft.Text(f"成功导出日历: {path}"), open=True))
            self.page.update()
        except Exception as ex:
            self.safe_status_update(f"导出失败: {ex}")

async def main(page: ft.Page):
    app = FletAppController(page)
    page.add(app.layout)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    ft.run(main)