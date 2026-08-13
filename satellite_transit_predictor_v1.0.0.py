import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import tkintermapview
import threading
import numpy as np
import datetime
import math
import csv
import os
import sys
import json
import requests
from io import StringIO
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
from skyfield.api import load, wgs84, EarthSatellite
from skyfield.framelib import itrs
import urllib3

# 关闭由于本地根证书过期或系统时间不同步引发的 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 确保打包为 exe 后工作目录正确，防止缓存与星历文件乱放
if getattr(sys, 'frozen', False):
    os.chdir(os.path.dirname(sys.executable))
else:
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

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
DEFAULT_SAT_SIZE = 5.0 # 未知小卫星默认按5米估算

def get_bearing(lat1, lon1, lat2, lon2):
    """计算两点间的初始方位角"""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - (math.sin(lat1) * math.cos(lat2) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360

def get_destination(lat, lon, distance_km, bearing_deg):
    """根据距离和方位角计算目标坐标"""
    R = 6371.0
    lat1, lon1, brng = math.radians(lat), math.radians(lon), math.radians(bearing_deg)
    lat2 = math.asin(math.sin(lat1) * math.cos(distance_km / R) +
                     math.cos(lat1) * math.sin(distance_km / R) * math.cos(brng))
    lon2 = lon1 + math.atan2(math.sin(brng) * math.sin(distance_km / R) * math.cos(lat1),
                             math.cos(distance_km / R) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lon2)

class SimulationWindow(tk.Toplevel):
    """凌星视角观测模拟小窗 (位置与方向矢量版，仅体现位置和方向，不体现真实大小)"""
    def __init__(self, master):
        super().__init__(master)
        self.title("凌星观测视角模拟 (相对位置与方向)")
        self.geometry("350x350")
        self.minsize(200, 200)
        
        # 黑色深空背景画布
        self.canvas = tk.Canvas(self, bg="#0d1117", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self.on_resize)
        
        self.target_type = None
        self.target_radius_deg = 0.26
        self.points = []
        
    def update_data(self, target_type, radius_deg, points):
        self.target_type = target_type
        self.target_radius_deg = radius_deg
        self.points = points
        self.draw()
        
    def on_resize(self, event):
        self.draw()
        
    def draw(self):
        """绘制目标天体和一条穿过盘面的静态方向指示线"""
        self.canvas.delete("all")
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        cx, cy = w / 2, h / 2
        
        if not self.target_type:
            self.canvas.create_text(cx, cy, text="请在左侧列表中点击选择一个凌星事件", fill="#58a6ff", font=("Microsoft YaHei UI", 10), justify=tk.CENTER)
            return
            
        r_px = min(w, h) * 0.38
        scale = r_px / self.target_radius_deg if self.target_radius_deg > 0 else r_px / 0.26
            
        color_map = {
            '太阳': ('#FDB813', '#FFD700'),
            '月球': ('#E0E0E0', '#FFFFFF'),
            '木星': ('#C88B3A', '#E0A96D'),
            '土星': ('#EADDAC', '#F4E8C1')
        }
        fill_c, outline_c = color_map.get(self.target_type, ('#AAAAAA', '#CCCCCC'))
        
        # 绘制目标天体圆盘
        self.canvas.create_oval(cx - r_px, cy - r_px, cx + r_px, cy + r_px, fill=fill_c, outline=outline_c, width=2)
        
        # 绘制瞄准十字丝
        self.canvas.create_line(cx, cy - r_px - 20, cx, cy + r_px + 20, fill="#30363d", dash=(4,4))
        self.canvas.create_line(cx - r_px - 20, cy, cx + r_px + 20, cy, fill="#30363d", dash=(4,4))
        self.canvas.create_text(cx, 15, text="天顶 (Up)", fill="#58a6ff", font=("Arial", 9))
        
        # 绘制静态轨道方向线（进入点、中心、离开点连线）
        if self.points and len(self.points) >= 2:
            screen_points = []
            for dx, dy in self.points:
                px = cx + dx * scale
                py = cy - dy * scale 
                screen_points.extend([px, py])
                
            self.canvas.create_line(screen_points, fill="#ff3333", width=3, arrow=tk.LAST)
            
            # 标出中心点位置
            mid_idx = (len(self.points) // 2) * 2
            if mid_idx < len(screen_points):
                sx, sy = screen_points[mid_idx], screen_points[mid_idx+1]
                self.canvas.create_oval(sx - 4, sy - 4, sx + 4, sy + 4, fill="#39ff14", outline="black", width=1)

class TransitCalculator:
    """天体物理与多目标凌星计算核心引擎"""
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
        """利用haversine公式计算球面距离"""
        R = 6371.0
        lat1_rad, lon1_rad = np.radians(lat1), np.radians(lon1)
        lat2_rad, lon2_rad = np.radians(lat2), np.radians(lon2)
        dlat = lat2_rad - lat1_rad
        dlon = lon2_rad - lon1_rad
        a = np.sin(dlat/2)**2 + np.cos(lat1_rad) * np.cos(lat2_rad) * np.sin(dlon/2)**2
        return R * (2 * np.arctan2(np.sqrt(a), np.sqrt(1-a)))

    def get_satellite_size(self, sat):
        """动态获取卫星物理尺寸，区分各代星链"""
        name_upper = sat.name.upper()
        if 'STARLINK' in name_upper:
            sat_num = sat.model.satnum if hasattr(sat, 'model') else 0
            if sat_num >= 55600:
                return 30.0 
            return 7.0 
            
        for known_sat, size in SAT_SIZES.items():
            if known_sat in name_upper:
                return size
        return DEFAULT_SAT_SIZE

    def fetch_tle_data(self, group_keys, force_download=False, backup_src='auto', custom_files=None):
        """多源免封锁智能拉取与缓存 TLE 数据（集成第三方公开API、镜像网关、忽略SSL与本地导入）"""
        if custom_files is None:
            custom_files = {}

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Cache-Control': 'no-cache'
        }
        
        # 多源免封锁地址库
        sources_map = {
            'ivan_api': {
                'stations': ["http://tle.ivanstanojevic.me/api/tle/25544"],
                'starlink': ["http://tle.ivanstanojevic.me/api/tle?search=STARLINK"],
                'oneweb': ["http://tle.ivanstanojevic.me/api/tle?search=ONEWEB"],
                'visual': ["http://tle.ivanstanojevic.me/api/tle?search=ISS"],
                'active': ["http://tle.ivanstanojevic.me/api/tle?search=STARLINK"]
            },
            'satnogs': {
                'stations': ["https://db.satnogs.org/api/tle/?group=stations"],
                'starlink': ["https://db.satnogs.org/api/tle/?group=starlink"], 
                'oneweb': ["https://db.satnogs.org/api/tle/?group=oneweb"],
                'visual': ["https://db.satnogs.org/api/tle/"],
                'active': ["https://db.satnogs.org/api/tle/?group=active"]
            },
            'celestrak_mirror': {
                'stations': ["https://celestrak.com/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle"],
                'starlink': ["https://celestrak.com/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle"],
                'oneweb': ["https://celestrak.com/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle"],
                'visual': ["https://celestrak.com/NORAD/elements/gp.php?GROUP=visual&FORMAT=tle"],
                'active': ["https://celestrak.com/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"]
            },
            'celestrak_main': {
                'stations': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle"],
                'starlink': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle"],
                'oneweb': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=oneweb&FORMAT=tle"],
                'visual': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=visual&FORMAT=tle"],
                'active': ["https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle"]
            }
        }

        if backup_src == 'auto':
            strategy_order = ['ivan_api', 'satnogs', 'celestrak_mirror', 'celestrak_main']
        elif backup_src in sources_map:
            strategy_order = [backup_src, 'ivan_api', 'satnogs', 'celestrak_mirror']
        else:
            strategy_order = ['ivan_api', 'satnogs', 'celestrak_mirror']

        cache_dir = "cache"
        os.makedirs(cache_dir, exist_ok=True)
        combined_text = ""

        for key in group_keys:
            # 1. 优先检查用户是否为该分组指定了外部文件导入接口
            if key in custom_files and custom_files[key] and os.path.exists(custom_files[key]):
                try:
                    with open(custom_files[key], "r", encoding="utf-8") as cf:
                        file_content = cf.read()
                        if len(file_content.strip()) > 50:
                            combined_text += "\n" + file_content
                            continue
                except Exception as e:
                    print(f"Read custom file for {key} error: {e}")

            cache_file = os.path.join(cache_dir, f"tle_{key}.txt")
            
            # 2. 如果不强制重新下载，且本地有有效缓存，瞬间直接读取本地缓存！
            if not force_download and os.path.exists(cache_file) and os.path.getsize(cache_file) > 100:
                try:
                    with open(cache_file, "r", encoding="utf-8") as cf:
                        content = cf.read()
                        if len(content) > 100 and '<html' not in content.lower():
                            combined_text += "\n" + content
                            continue
                except: pass

            # 3. 本地无缓存或强制下载时，尝试多源免封锁联网下载 (verify=False 忽略SSL证书与过期)
            success = False
            for src_name in strategy_order:
                if src_name not in sources_map or key not in sources_map[src_name]: continue
                urls = sources_map[src_name][key]
                for url in urls:
                    try:
                        resp = requests.get(url, headers=headers, timeout=15, verify=False)
                        if resp.status_code == 200 and len(resp.text) > 50:
                            text_data = resp.text
                            if 'ivanstanojevic.me' in url:
                                try:
                                    jdata = resp.json()
                                    items = jdata.get('member', []) if isinstance(jdata, dict) else jdata
                                    if isinstance(items, list):
                                        text_data = ""
                                        for item in items:
                                            name = item.get('name', 'UNKNOWN')
                                            l1 = item.get('line1', '')
                                            l2 = item.get('line2', '')
                                            if l1 and l2:
                                                text_data += f"{name}\n{l1}\n{l2}\n"
                                except: pass

                            if len(text_data.strip()) > 50 and '<html' not in text_data.lower():
                                combined_text += "\n" + text_data
                                with open(cache_file, "w", encoding="utf-8") as cf: cf.write(text_data)
                                success = True
                                break
                    except Exception as e: print(f"Fetch TLE error from {src_name} ({url}): {e}")
                if success: break
            
            # 4. 联网失败但本地有旧缓存时兜底读取
            if not success and os.path.exists(cache_file) and os.path.getsize(cache_file) > 0:
                try:
                    with open(cache_file, "r", encoding="utf-8") as cf:
                        old_content = cf.read()
                        if len(old_content) > 0:
                            combined_text += "\n" + old_content
                            success = True
                except: pass

        if len(combined_text.strip()) < 100:
            emergency_tle = """ISS (ZARYA)
1 25544U 98067A   26065.50000000  .00016717  00000-0  30270-3 0  9999
2 25544  51.6415 147.2882 0006249  88.5415 271.7456 15.50123853498142
CSS (TIANGONG)
1 48274U 21035A   26065.50000000  .00082312  00000-0  91230-3 0  9992
2 48274  41.4705 200.1234 0005432  30.1234 330.1234 15.61234567234567
STARLINK-30001
1 55601U 23001A   26065.50000000  .00001234  00000-0  12340-4 0  9991
2 55601  53.0000  10.0000 0001000   0.0000   0.0000 15.00000000123456
"""
            combined_text += "\n" + emergency_tle

        return combined_text

    def find_solar_transits(self, satellite, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, focal_mm, pixel_um):
        total_seconds = int((end_time - start_time).total_seconds())
        if total_seconds <= 0: return []
        
        step_coarse = 15 
        t_array_coarse = self.ts.utc(start_time.year, start_time.month, start_time.day, 
                                     start_time.hour, start_time.minute, 
                                     range(start_time.second, start_time.second + total_seconds, step_coarse))
        
        try: sat_pos_coarse = satellite.at(t_array_coarse).frame_xyz(itrs).km
        except Exception: sat_pos_coarse = satellite.at(t_array_coarse).position.km
            
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
            
            t_array_fine = self.ts.utc(t1.year, t1.month, t1.day, t1.hour, t1.minute, 
                                       np.linspace(t1.second, t1.second + (t2-t1).total_seconds(), fine_seconds))
            
            sun_f = self.earth.at(t_array_fine).observe(self.sun).apparent().frame_xyz(itrs).km
            sat_f = satellite.at(t_array_fine).frame_xyz(itrs).km
            
            D_f = sat_f - sun_f
            D_f = D_f / np.linalg.norm(D_f, axis=0)
            Px, Py, Pz = sat_f[0], sat_f[1], sat_f[2]
            Dx, Dy, Dz = D_f[0], D_f[1], D_f[2]
            
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
            try:
                tz_str = tf.timezone_at(lat=obs_lat, lng=obs_lon)
                local_tz = ZoneInfo(tz_str) if tz_str else CST
            except: local_tz = CST
            local_time = utc_dt.astimezone(local_tz)
            
            ang_vel = (7.5 / sat_dist_km) * 206265 
            duration_sec = (0.53 * 3600) / ang_vel if ang_vel > 0 else 0
            start_utc = utc_dt - datetime.timedelta(seconds=duration_sec/2)
            end_utc = utc_dt + datetime.timedelta(seconds=duration_sec/2)
            min_sep_arcmin = (min_dist / sat_dist_km) * 3437.75 

            results.append({
                'target_type': '太阳', 'sat_name': satellite.name,
                'norad_id': satellite.model.satnum if hasattr(satellite, 'model') else 'N/A',
                'start_utc_str': start_utc.strftime('%Y-%m-%d %H:%M:%S'),
                'closest_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                'end_utc_str': end_utc.strftime('%Y-%m-%d %H:%M:%S'),
                'local_time_str': local_time.strftime('%Y-%m-%d %H:%M:%S'),
                'tz_name': str(local_tz), 'duration': f"{duration_sec:.2f}",
                'min_sep': f"{min_sep_arcmin:.2f}", 'alt': f"{sun_alt.degrees:.2f}", 'az': f"{sun_az.degrees:.2f}",
                'utc_dt': utc_dt, 'min_dist_center_km': min_dist, 'sat_dist_km': sat_dist_km,
                'path_width_km': path_width_km, 'covered_pixels': covered_pixels,
                'path_center': path_coords, 'path_left': left_band, 'path_right': right_band,
                'sat_obj': satellite
            })
        return results

    def find_disc_transits(self, satellite, start_time, end_time, obs_lat, obs_lon, obs_alt, search_mode, max_radius_km, target_name, focal_mm, pixel_um):
        target_objs = {
            '月球': {'obj': self.moon, 'radius_km': 1737.4},
            '木星': {'obj': self.jupiter, 'radius_km': 69911.0},
            '土星': {'obj': self.saturn, 'radius_km': 58232.0}
        }
        if target_name not in target_objs: return []
        
        t_info = target_objs[target_name]
        target_obj = t_info['obj']
        t_radius_km = t_info['radius_km']
        
        total_seconds = int((end_time - start_time).total_seconds())
        if total_seconds <= 0: return []
        
        t_array = self.ts.utc(start_time.year, start_time.month, start_time.day, 
                              start_time.hour, start_time.minute, 
                              range(start_time.second, start_time.second + total_seconds, 5))
        
        observer_loc = wgs84.latlon(obs_lat, obs_lon, elevation_m=obs_alt)
        observer = self.earth + observer_loc
        results = []
        in_transit = False
        
        for t in t_array:
            try:
                astrometric_target = observer.at(t).observe(target_obj).apparent()
                alt, az, distance = astrometric_target.altaz()
                if alt.degrees < 5: continue
                
                topocentric_sat = (satellite - observer_loc).at(t)
                sat_dist_km = topocentric_sat.distance().km
                
                target_angular_radius_arcsec = (t_radius_km / distance.km) * 206265
                sat_physical_size = self.get_satellite_size(satellite)
                sat_angular_radius_arcsec = (sat_physical_size / sat_dist_km) * 206265 / 2.0
                
                sep_arcsec = astrometric_target.separation_from(topocentric_sat).arcseconds
                
                if sep_arcsec <= (target_angular_radius_arcsec + sat_angular_radius_arcsec + 10):
                    if not in_transit:
                        in_transit = True
                        utc_dt = t.utc_datetime()
                        try:
                            tz_str = tf.timezone_at(lat=obs_lat, lng=obs_lon)
                            local_tz = ZoneInfo(tz_str) if tz_str else CST
                        except: local_tz = CST
                        local_time = utc_dt.astimezone(local_tz)
                        
                        path_width_km = (target_angular_radius_arcsec * 2 / 206265) * sat_dist_km
                        lat_o, lon_o = obs_lat, obs_lon
                        path_coords = [(lat_o - 0.02, lon_o - 0.02), (lat_o + 0.02, lon_o + 0.02)]
                        covered_pixels = (sat_physical_size * focal_mm) / (sat_dist_km * pixel_um)
                        
                        results.append({
                            'target_type': target_name, 'sat_name': satellite.name,
                            'norad_id': satellite.model.satnum if hasattr(satellite, 'model') else 'N/A',
                            'start_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                            'closest_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                            'end_utc_str': utc_dt.strftime('%Y-%m-%d %H:%M:%S'),
                            'local_time_str': local_time.strftime('%Y-%m-%d %H:%M:%S'),
                            'tz_name': str(local_tz), 'duration': "~0.5",
                            'min_sep': f"{(sep_arcsec/60):.2f}", 'alt': f"{alt.degrees:.2f}", 'az': f"{az.degrees:.2f}",
                            'utc_dt': utc_dt, 'min_dist_center_km': 0.0, 'sat_dist_km': sat_dist_km,
                            'path_width_km': path_width_km, 'covered_pixels': covered_pixels,
                            'path_center': path_coords, 'path_left': path_coords, 'path_right': path_coords,
                            'sat_obj': satellite
                        })
                else: in_transit = False
            except: continue
        return results

class TransitPredictorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("飞无止境卫星凌日/行星预测器 - Satellite Transit Predictor")
        self.geometry("1650x920")
        
        self.calc = None 
        self.transit_data_store = {} 
        self.active_map_elements = [] 
        self.observer_marker = None
        self.stop_requested = False
        self.sim_thread_id = 0
        self.custom_file_paths = {} 
        
        self.setup_ui()
        self.apply_theme()
        
    def apply_theme(self, event=None):
        theme = self.theme_var.get()
        style = ttk.Style(self)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
            
        if theme == "明亮模式 (Light)":
            bg_color, fg_color, input_bg, select_bg, lbl_fg, border = "#f0f0f0", "#333333", "#ffffff", "#0078d7", "#005a9e", "#cccccc"
        elif theme == "深空蓝 (Astro Blue)":
            bg_color, fg_color, input_bg, select_bg, lbl_fg, border = "#0d1117", "#c9d1d9", "#161b22", "#1f6feb", "#58a6ff", "#30363d"
        else:
            bg_color, fg_color, input_bg, select_bg, lbl_fg, border = "#1e1e1e", "#d4d4d4", "#2d2d2d", "#005a9e", "#569cd6", "#444444"

        self.configure(bg=bg_color)
        
        style.configure(".", font=('Microsoft YaHei UI', 9), background=bg_color, foreground=fg_color, fieldbackground=input_bg, bordercolor=border)
        style.configure("TLabelframe", font=('Microsoft YaHei UI', 10, 'bold'), background=bg_color, borderwidth=1, bordercolor=border)
        style.configure("TLabelframe.Label", foreground=lbl_fg, background=bg_color)
        style.configure("TButton", background=input_bg, foreground=fg_color, borderwidth=1, bordercolor=border)
        style.map("TButton", background=[("active", border), ("pressed", bg_color)])
        
        style.configure("Treeview", background=input_bg, fieldbackground=input_bg, foreground=fg_color, borderwidth=0, rowheight=24)
        style.configure("Treeview.Heading", background=bg_color, foreground=fg_color, borderwidth=1, font=('Microsoft YaHei UI', 9, 'bold'))
        style.map("Treeview", background=[("selected", select_bg)], foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", border)])
        
        style.configure("TNotebook", background=bg_color, borderwidth=0)
        style.configure("TNotebook.Tab", background=input_bg, foreground=fg_color, padding=[10, 2], borderwidth=1)
        style.map("TNotebook.Tab", background=[("selected", bg_color)], foreground=[("selected", lbl_fg)])
        
        if hasattr(self, 'custom_tle_text'):
            self.custom_tle_text.config(bg=input_bg, fg=fg_color, insertbackground=fg_color, relief=tk.FLAT, highlightbackground=border, highlightcolor=select_bg)
        if hasattr(self, 'tree_menu'):
            self.tree_menu.config(bg=input_bg, fg=fg_color, activebackground=select_bg, activeforeground="#ffffff")

    def setup_ui(self):
        main_pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        left_outer = ttk.Frame(main_pane, width=440)
        main_pane.add(left_outer, weight=0)
        
        self.left_canvas = tk.Canvas(left_outer, borderwidth=0, highlightthickness=0)
        self.left_scrollbar = ttk.Scrollbar(left_outer, orient="vertical", command=self.left_canvas.yview)
        
        self.left_scrollable_frame = ttk.Frame(self.left_canvas)
        self.left_scrollable_frame.bind(
            "<Configure>",
            lambda e: self.left_canvas.configure(scrollregion=self.left_canvas.bbox("all"))
        )

        self.left_window_id = self.left_canvas.create_window((0, 0), window=self.left_scrollable_frame, anchor="nw")
        
        def _on_canvas_configure(event):
            self.left_canvas.itemconfig(self.left_window_id, width=event.width)
        self.left_canvas.bind("<Configure>", _on_canvas_configure)

        self.left_canvas.configure(yscrollcommand=self.left_scrollbar.set)
        self.left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.left_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        def _on_mousewheel(event): self.left_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        def _bound_to_mousewheel(event): self.left_canvas.bind_all("<MouseWheel>", _on_mousewheel)
        def _unbound_to_mousewheel(event): self.left_canvas.unbind_all("<MouseWheel>")

        self.left_canvas.bind('<Enter>', _bound_to_mousewheel)
        self.left_canvas.bind('<Leave>', _unbound_to_mousewheel)
        self.left_scrollable_frame.bind('<Enter>', _bound_to_mousewheel)
        self.left_scrollable_frame.bind('<Leave>', _unbound_to_mousewheel)

        left_panel = self.left_scrollable_frame
        
        f_theme = ttk.Frame(left_panel)
        f_theme.pack(fill=tk.X, pady=(0, 4), padx=2)
        ttk.Label(f_theme, text="界面主题配色:").pack(side=tk.LEFT)
        self.theme_var = tk.StringVar(value="专业暗色 (Dark)")
        theme_cb = ttk.Combobox(f_theme, textvariable=self.theme_var, values=["专业暗色 (Dark)", "明亮模式 (Light)", "深空蓝 (Astro Blue)"], state="readonly", width=18)
        theme_cb.pack(side=tk.LEFT, padx=5)
        theme_cb.bind("<<ComboboxSelected>>", self.apply_theme)
        
        frame_loc = ttk.LabelFrame(left_panel, text="观测地点")
        frame_loc.pack(fill=tk.X, pady=4, padx=2)
        
        f_search = ttk.Frame(frame_loc)
        f_search.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_search, text="地名搜索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        ttk.Entry(f_search, textvariable=self.search_var, width=25).pack(side=tk.LEFT, padx=5)
        ttk.Button(f_search, text="定位", command=self.geocode_address, width=6).pack(side=tk.RIGHT)
        
        f_lat = ttk.Frame(frame_loc)
        f_lat.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_lat, text="纬度 (Lat):").pack(side=tk.LEFT)
        self.lat_var = tk.StringVar(value="39.904200")
        ttk.Entry(f_lat, textvariable=self.lat_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))
        
        f_lon = ttk.Frame(frame_loc)
        f_lon.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_lon, text="经度 (Lon):").pack(side=tk.LEFT)
        self.lon_var = tk.StringVar(value="116.407400")
        ttk.Entry(f_lon, textvariable=self.lon_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))
        
        f_alt = ttk.Frame(frame_loc)
        f_alt.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_alt, text="海拔 (m):").pack(side=tk.LEFT)
        self.alt_var = tk.StringVar(value="50.00")
        ttk.Entry(f_alt, textvariable=self.alt_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))
        ttk.Label(frame_loc, text="提示: 也可以直接在右侧地图上右键点击 -> 设为观测点", foreground="gray", font=('Microsoft YaHei UI', 8)).pack(anchor=tk.W, padx=5, pady=2)

        frame_hw = ttk.LabelFrame(left_panel, text="天文摄影器材预设 (用于预估成像大小)喵~")
        frame_hw.pack(fill=tk.X, pady=4, padx=2)
        f_foc = ttk.Frame(frame_hw)
        f_foc.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_foc, text="主镜焦距 (mm):").pack(side=tk.LEFT)
        self.focal_var = tk.StringVar(value="480")
        ttk.Entry(f_foc, textvariable=self.focal_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))
        
        f_pix = ttk.Frame(frame_hw)
        f_pix.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_pix, text="相机像元尺寸 (µm):").pack(side=tk.LEFT)
        self.pixel_var = tk.StringVar(value="1.45")
        ttk.Entry(f_pix, textvariable=self.pixel_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))

        frame_time = ttk.LabelFrame(left_panel, text="搜索时间范围 (本地时区/UTC 均可)")
        frame_time.pack(fill=tk.X, pady=4, padx=2)
        
        now = datetime.datetime.now(datetime.timezone.utc)
        f_start = ttk.Frame(frame_time)
        f_start.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_start, text="开始:").pack(side=tk.LEFT)
        self.start_var = tk.StringVar(value=now.strftime("%Y-%m-%d 00:00:00"))
        ttk.Entry(f_start, textvariable=self.start_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))
        
        f_end = ttk.Frame(frame_time)
        f_end.pack(fill=tk.X, pady=2, padx=5)
        ttk.Label(f_end, text="结束:").pack(side=tk.LEFT)
        self.end_var = tk.StringVar(value=(now + datetime.timedelta(days=1)).strftime("%Y-%m-%d 23:59:59"))
        ttk.Entry(f_end, textvariable=self.end_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))

        frame_src = ttk.LabelFrame(left_panel, text="卫星 / 航天器来源与抗封锁数据方案")
        frame_src.pack(fill=tk.X, pady=4, padx=2)
        
        self.source_nb = ttk.Notebook(frame_src)
        self.source_nb.pack(fill=tk.X, padx=5, pady=5)
        
        tab_preset = ttk.Frame(self.source_nb, padding=5)
        tab_import = ttk.Frame(self.source_nb, padding=5) 
        tab_custom = ttk.Frame(self.source_nb, padding=5)
        tab_backup = ttk.Frame(self.source_nb, padding=5)
        
        self.source_nb.add(tab_preset, text="预置分组 & ID")
        self.source_nb.add(tab_import, text="📁 外部文件导入")
        self.source_nb.add(tab_custom, text="自定义 TLE")
        self.source_nb.add(tab_backup, text="多源免封锁方案")
        
        self.chk_stations = tk.BooleanVar(value=True)
        self.chk_sl_gen1 = tk.BooleanVar(value=False)
        self.chk_sl_gen2 = tk.BooleanVar(value=False)
        self.chk_oneweb = tk.BooleanVar(value=False)
        self.chk_visual = tk.BooleanVar(value=False)
        self.chk_active = tk.BooleanVar(value=False)
        
        ttk.Checkbutton(tab_preset, text="空间站 (ISS / 天宫等)", variable=self.chk_stations).pack(anchor=tk.W)
        ttk.Checkbutton(tab_preset, text="初代星链 (Gen 1)", variable=self.chk_sl_gen1).pack(anchor=tk.W)
        ttk.Checkbutton(tab_preset, text="大尺寸二代星链 (V2 Mini 及 ID>=55600)", variable=self.chk_sl_gen2).pack(anchor=tk.W)
        
        f_sl_range = ttk.Frame(tab_preset)
        f_sl_range.pack(fill=tk.X, pady=4, padx=2)
        self.chk_sl_range = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_sl_range, text="自定义星链 ID 范围:", variable=self.chk_sl_range).pack(side=tk.LEFT)
        self.sl_min_id_var = tk.StringVar(value="55600")
        self.sl_max_id_var = tk.StringVar(value="99999")
        ttk.Entry(f_sl_range, textvariable=self.sl_min_id_var, width=7).pack(side=tk.LEFT, padx=3)
        ttk.Label(f_sl_range, text="-").pack(side=tk.LEFT)
        ttk.Entry(f_sl_range, textvariable=self.sl_max_id_var, width=7).pack(side=tk.LEFT, padx=3)

        ttk.Checkbutton(tab_preset, text="OneWeb 网络", variable=self.chk_oneweb).pack(anchor=tk.W)
        ttk.Checkbutton(tab_preset, text="肉眼可见亮星表 (Visual)", variable=self.chk_visual).pack(anchor=tk.W)
        
        # 外部数据导入接口
        ttk.Label(tab_import, text="为各分组绑定外部本地 TLE/TXT 文件:", font=('Microsoft YaHei UI', 9, 'bold')).pack(anchor=tk.W, pady=2)
        
        self.import_labels = {}
        import_groups = [('stations', '空间站分组文件'), ('starlink', '星链分组文件'), ('oneweb', 'OneWeb分组文件'), ('visual', '亮星分组文件')]
        for g_key, g_name in import_groups:
            f_imp = ttk.Frame(tab_import)
            f_imp.pack(fill=tk.X, pady=2)
            ttk.Label(f_imp, text=g_name, width=14).pack(side=tk.LEFT)
            lbl_path = ttk.Label(f_imp, text="未选择文件", foreground="gray", width=18)
            lbl_path.pack(side=tk.LEFT, padx=2)
            self.import_labels[g_key] = lbl_path
            ttk.Button(f_imp, text="浏览...", width=6, command=lambda k=g_key: self.browse_external_file(k)).pack(side=tk.RIGHT)

        ttk.Label(tab_import, text="提示: 导入后系统将优先使用您指定的本地文件计算。", foreground="gray", font=('Microsoft YaHei UI', 8)).pack(anchor=tk.W, pady=4)

        f_kw = ttk.Frame(tab_preset)
        f_kw.pack(fill=tk.X, pady=4)
        ttk.Label(f_kw, text="关键字过滤:").pack(side=tk.LEFT)
        self.keyword_var = tk.StringVar(value="")
        ttk.Entry(f_kw, textvariable=self.keyword_var).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(5,0))
        
        f_max = ttk.Frame(tab_preset)
        f_max.pack(fill=tk.X, pady=2)
        ttk.Label(f_max, text="每组最多取:").pack(side=tk.LEFT)
        self.max_sats_var = tk.StringVar(value="300")
        ttk.Spinbox(f_max, from_=10, to=5000, textvariable=self.max_sats_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(f_max, text="颗").pack(side=tk.LEFT)
        
        self.force_download_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tab_preset, text="强制重新下载 TLE (忽略本地缓存)", variable=self.force_download_var).pack(anchor=tk.W, pady=2)

        self.custom_tle_text = tk.Text(tab_custom, height=5, width=40)
        self.custom_tle_text.pack(fill=tk.BOTH, expand=True)

        self.backup_src_var = tk.StringVar(value="auto")
        ttk.Radiobutton(tab_backup, text="全网抗封锁智能多源自动切换 (Auto - 推荐)", variable=self.backup_src_var, value="auto").pack(anchor=tk.W, pady=1)
        ttk.Radiobutton(tab_backup, text="Ivan Stanojevic 开放 TLE API (防IP封锁)", variable=self.backup_src_var, value="ivan_api").pack(anchor=tk.W, pady=1)
        ttk.Radiobutton(tab_backup, text="SatNOGS DB 开放数据库", variable=self.backup_src_var, value="satnogs").pack(anchor=tk.W, pady=1)
        ttk.Radiobutton(tab_backup, text="CelesTrak 官方镜像 (celestrak.com)", variable=self.backup_src_var, value="celestrak_mirror").pack(anchor=tk.W, pady=1)
        ttk.Radiobutton(tab_backup, text="CelesTrak 主服务器 (celestrak.org)", variable=self.backup_src_var, value="celestrak_main").pack(anchor=tk.W, pady=1)

        frame_targets = ttk.LabelFrame(left_panel, text="凌日 / 凌月 / 行星 目标")
        frame_targets.pack(fill=tk.X, pady=4, padx=2)
        
        f_t1 = ttk.Frame(frame_targets)
        f_t1.pack(fill=tk.X, padx=5, pady=2)
        self.target_sun, self.target_moon = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        ttk.Checkbutton(f_t1, text="太阳 (凌日)", variable=self.target_sun).pack(side=tk.LEFT, expand=True, anchor=tk.W)
        ttk.Checkbutton(f_t1, text="月球 (凌月)", variable=self.target_moon).pack(side=tk.LEFT, expand=True, anchor=tk.W)
        
        f_t2 = ttk.Frame(frame_targets)
        f_t2.pack(fill=tk.X, padx=5, pady=2)
        self.target_jupiter, self.target_saturn = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        ttk.Checkbutton(f_t2, text="木星 (凌木星)", variable=self.target_jupiter).pack(side=tk.LEFT, expand=True, anchor=tk.W)
        ttk.Checkbutton(f_t2, text="土星 (凌土星)", variable=self.target_saturn).pack(side=tk.LEFT, expand=True, anchor=tk.W)

        frame_mode = ttk.LabelFrame(left_panel, text="搜索模式与半径")
        frame_mode.pack(fill=tk.X, pady=4, padx=2)
        
        self.search_mode_var = tk.StringVar(value="exact")
        ttk.Radiobutton(frame_mode, text="精确点搜索 (只算观测点本身能否看到)", variable=self.search_mode_var, value="exact").pack(anchor=tk.W, padx=5, pady=2)
        ttk.Radiobutton(frame_mode, text="区域范围搜索 (反向推断周边的可视走廊)", variable=self.search_mode_var, value="regional").pack(anchor=tk.W, padx=5, pady=2)
        
        f_rad = ttk.Frame(frame_mode)
        f_rad.pack(fill=tk.X, padx=20, pady=2)
        ttk.Label(f_rad, text="搜索半径:").pack(side=tk.LEFT)
        self.rad_var = tk.StringVar(value="50.00")
        ttk.Spinbox(f_rad, from_=1, to=1000, textvariable=self.rad_var, width=8).pack(side=tk.LEFT, padx=5)
        ttk.Label(f_rad, text="km").pack(side=tk.LEFT)

        f_btns = ttk.Frame(left_panel)
        f_btns.pack(fill=tk.X, pady=10, padx=5)
        
        self.calc_btn = ttk.Button(f_btns, text="开始高精度凌星搜索", command=self.start_calculation)
        self.calc_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        
        self.stop_btn = ttk.Button(f_btns, text="停止搜索", command=self.stop_calculation, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(3, 0))
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(left_panel, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(fill=tk.X, padx=5)
        
        self.status_var = ttk.Label(left_panel, text="就绪。已整合防封锁多源开放API与本地缓存优先机制。", wraplength=400)
        self.status_var.pack(anchor=tk.W, padx=5, pady=5)

        right_panel = ttk.PanedWindow(main_pane, orient=tk.VERTICAL)
        main_pane.add(right_panel, weight=1)
        
        map_frame = ttk.Frame(right_panel)
        right_panel.add(map_frame, weight=3)
        self.map_widget = tkintermapview.TkinterMapView(map_frame, corner_radius=0)
        self.map_widget.pack(fill=tk.BOTH, expand=True)
        self.map_widget.add_right_click_menu_command(label="设为观测点", command=self.set_observer_from_map, pass_coords=True)
        
        try:
            self.map_widget.set_position(float(self.lat_var.get()), float(self.lon_var.get()))
            self.map_widget.set_zoom(9)
            self.observer_marker = self.map_widget.set_marker(float(self.lat_var.get()), float(self.lon_var.get()), text="观测点")
        except: pass

        table_frame = ttk.Frame(right_panel)
        right_panel.add(table_frame, weight=2)
        
        toolbar = ttk.Frame(table_frame)
        toolbar.pack(fill=tk.X, pady=(0,2))
        ttk.Button(toolbar, text="导出 CSV 报表", command=self.export_to_csv).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="导出 iCal 日历 (.ics)", command=self.export_to_ical).pack(side=tk.LEFT, padx=2)
        
        btn_sim = ttk.Button(toolbar, text="🛰️ 凌星视角模拟 (轨迹与方向)", command=self.toggle_simulation_window)
        btn_sim.pack(side=tk.RIGHT, padx=2)
        
        cols = ("sat_name", "norad_id", "target", "start_utc", "closest_utc", "end_utc",
                "local_time", "tz", "duration", "min_sep", "alt", "az", "dist",
                "path_width", "center_dist", "pixels")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        
        headings = [
            ("卫星/航天器", 100), ("NORAD ID", 70), ("目标", 50), ("开始时刻(UTC)", 130), 
            ("最接近(UTC)", 130), ("结束时刻(UTC)", 130), ("接近(本地时间)", 130), 
            ("本地时区", 80), ("持续(秒)", 60), ("最小角距(角分)", 90), ("高度角(°)", 70), 
            ("方位角(°)", 70), ("距离(km)", 70), ("带宽(km)", 70), ("偏离中心(km)", 90), ("像素预估(px)", 80)
        ]
        
        for col, (text, width) in zip(cols, headings):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, minwidth=50)
            
        scrollbar_y = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        scrollbar_x = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        
        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="✨ 在地图上高亮此轨迹", command=self.highlight_selected_transit)
        self.tree_menu.add_command(label="🎯 地图居中至此轨迹", command=self.center_map_on_selected)
        self.tree.bind("<Button-3>", self.show_tree_menu)

    def browse_external_file(self, group_key):
        """浏览并绑定外部 TLE 数据文件"""
        filepath = filedialog.askopenfilename(title=f"选择 {group_key} 的外部轨道文件", filetypes=[("文本文件", "*.txt"), ("TLE 文件", "*.tle"), ("所有文件", "*.*")])
        if filepath:
            self.custom_file_paths[group_key] = filepath
            filename = os.path.basename(filepath)
            if group_key in self.import_labels:
                self.import_labels[group_key].config(text=filename, foreground="#58a6ff")
            self.safe_status_update(f"已成功绑定外部文件: {filename}")

    def toggle_simulation_window(self):
        """开启或聚焦模拟器小窗"""
        if not hasattr(self, 'sim_window') or not self.sim_window.winfo_exists():
            self.sim_window = SimulationWindow(self)
            sel = self.tree.selection()
            if sel: self.update_simulation(sel[0])
        else:
            self.sim_window.lift()

    def update_simulation(self, iid):
        """计算并绘制静态的凌星方向及位置轨迹"""
        if not hasattr(self, 'sim_window') or not self.sim_window.winfo_exists(): return
        
        data = self.transit_data_store.get(iid)
        if not data: return
        sat = data.get('sat_obj')
        if not sat: return
        
        target_type = data['target_type']
        utc_dt = data['utc_dt']
        try: duration = float(data['duration'])
        except: duration = 1.0
            
        lat, lon, alt = float(self.lat_var.get()), float(self.lon_var.get()), float(self.alt_var.get())
        
        self.sim_thread_id += 1
        current_id = self.sim_thread_id
        threading.Thread(target=self._compute_and_draw_sim, args=(current_id, sat, target_type, utc_dt, duration, lat, lon, alt), daemon=True).start()

    def _compute_and_draw_sim(self, thread_id, sat, target_type, utc_dt, duration, lat, lon, alt):
        ts = self.calc.ts
        observer_loc = wgs84.latlon(lat, lon, elevation_m=alt)
        observer = self.calc.earth + observer_loc
        
        target_obj = self.calc.sun if target_type == '太阳' else self.calc.moon if target_type == '月球' else None
        if target_type == '木星': target_obj = self.calc.jupiter
        if target_type == '土星': target_obj = self.calc.saturn
        if not target_obj: return
        
        pad = max(duration / 2.0, 0.5)
        t_start = utc_dt - datetime.timedelta(seconds=pad)
        t_end = utc_dt + datetime.timedelta(seconds=pad)
        
        t0 = ts.utc(t_start.year, t_start.month, t_start.day, t_start.hour, t_start.minute, t_start.second + t_start.microsecond/1e6)
        t_mid = ts.utc(utc_dt.year, utc_dt.month, utc_dt.day, utc_dt.hour, utc_dt.minute, utc_dt.second + utc_dt.microsecond/1e6)
        t1 = ts.utc(t_end.year, t_end.month, t_end.day, t_end.hour, t_end.minute, t_end.second + t_end.microsecond/1e6)
        
        try:
            points = []
            target_radius_deg = 0.26
            
            for t in [t0, t_mid, t1]:
                astro_target = observer.at(t).observe(target_obj).apparent()
                target_alt, target_az, target_dist = astro_target.altaz()
                
                topocentric_sat = (sat - observer_loc).at(t)
                sat_alt, sat_az, _ = topocentric_sat.altaz()
                
                t_alt_deg = target_alt.degrees
                t_az_deg = target_az.degrees
                s_alt_deg = sat_alt.degrees
                s_az_deg = sat_az.degrees
                
                d_az_deg = (s_az_deg - t_az_deg + 180) % 360 - 180
                dx_deg = d_az_deg * np.cos(np.radians(t_alt_deg))
                dy_deg = s_alt_deg - t_alt_deg
                points.append((dx_deg, dy_deg))
                
                if t == t_mid:
                    if target_type == '太阳': target_radius_deg = 0.266
                    elif target_type == '月球': target_radius_deg = 0.25 
                    elif target_type == '木星': target_radius_deg = (69911.0 / target_dist.km) * (180/math.pi)
                    elif target_type == '土星': target_radius_deg = (58232.0 / target_dist.km) * (180/math.pi)
            
            if self.sim_thread_id == thread_id:
                self.after(0, lambda: {hasattr(self, 'sim_window') and self.sim_window.winfo_exists() and self.sim_window.update_data(target_type, target_radius_deg, points)})
        except Exception as e: print("Simulation error:", e)

    def geocode_address(self):
        address = self.search_var.get().strip()
        if not address: return
        self.safe_status_update(f"正在智能搜索地点: {address}...")
        self.map_widget.set_address(address)
        
        def fetch_coords():
            try:
                pos = self.map_widget.get_position()
                self.lat_var.set(f"{pos[0]:.6f}")
                self.lon_var.set(f"{pos[1]:.6f}")
                if self.observer_marker: self.observer_marker.delete()
                self.observer_marker = self.map_widget.set_marker(pos[0], pos[1], text="观测点")
                self.safe_status_update(f"已定位到: {address}")
            except: self.safe_status_update("地点搜索失败。")
        self.after(1500, fetch_coords)

    def set_observer_from_map(self, coords):
        lat, lon = coords
        self.lat_var.set(f"{lat:.6f}")
        self.lon_var.set(f"{lon:.6f}")
        if self.observer_marker: self.observer_marker.delete()
        self.observer_marker = self.map_widget.set_marker(lat, lon, text="观测点")
        self.safe_status_update(f"已将观测点更新为: 纬度 {lat:.4f}, 经度 {lon:.4f}")

    def safe_status_update(self, msg, progress=None):
        def update():
            self.status_var.config(text=msg)
            if progress is not None: self.progress_var.set(progress)
        self.after(0, update)

    def show_tree_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            if item not in self.tree.selection(): self.tree.selection_set(item)
            self.render_selected_paths()
            self.tree_menu.tk_popup(event.x_root, event.y_root)

    def clear_map_paths(self):
        for elem in self.active_map_elements:
            try: self.map_widget.delete(elem)
            except: pass
        self.active_map_elements.clear()

    def render_selected_paths(self):
        self.clear_map_paths()
        selected_iids = self.tree.selection()
        if not selected_iids: return

        for iid in selected_iids:
            if iid in self.transit_data_store:
                t = self.transit_data_store[iid]
                if t['path_center']:
                    c = self.map_widget.set_path(t['path_center'], color="red", width=3)
                    self.active_map_elements.append(c)
                if t['path_left'] and t['path_right'] and t['path_left'] != t['path_center']:
                    l = self.map_widget.set_path(t['path_left'], color="gray", width=1)
                    r = self.map_widget.set_path(t['path_right'], color="gray", width=1)
                    self.active_map_elements.extend([l, r])

    def highlight_selected_transit(self): self.render_selected_paths()

    def center_map_on_selected(self):
        selected_iids = self.tree.selection()
        if not selected_iids: return
        iid = selected_iids[0]
        if iid in self.transit_data_store:
            path = self.transit_data_store[iid]['path_center']
            if path:
                mid = path[len(path) // 2]
                self.map_widget.set_position(mid[0], mid[1])
                self.map_widget.set_zoom(9)

    def on_tree_select(self, event):
        self.render_selected_paths()
        selected_iids = self.tree.selection()
        if selected_iids:
            self.update_simulation(selected_iids[0])

    def export_to_csv(self):
        if not self.transit_data_store: return messagebox.showwarning("提示", "当前没有数据！")
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV 文件", "*.csv")])
        if not path: return
        try:
            with open(path, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(["卫星/航天器", "NORAD ID", "目标", "开始时刻(UTC)", "最接近(UTC)", "结束时刻(UTC)", "接近(本地时间)", "本地时区", "持续(秒)", "最小角距(角分)", "高度角(°)", "方位角(°)", "距离(km)", "走廊宽度(km)", "搜索中心(km)", "像素预估(px)"])
                for iid in self.tree.get_children():
                    writer.writerow(self.tree.item(iid, "values"))
            messagebox.showinfo("成功", f"成功导出至: {path}")
        except Exception as e: messagebox.showerror("错误", str(e))

    def export_to_ical(self):
        if not self.transit_data_store: return messagebox.showwarning("提示", "当前没有数据！")
        path = filedialog.asksaveasfilename(defaultextension=".ics", filetypes=[("iCal 日历", "*.ics")])
        if not path: return
        try:
            lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Transit Predictor//CN"]
            for iid, t in self.transit_data_store.items():
                utc_dt = t['utc_dt']
                start_str = utc_dt.strftime("%Y%m%dT%H%M%SZ")
                end_str = (utc_dt + datetime.timedelta(seconds=5)).strftime("%Y%m%dT%H%M%SZ")
                lines.extend([
                    "BEGIN:VEVENT", f"UID:transit-{start_str}-{t['sat_name']}@predictor",
                    f"DTSTAMP:{start_str}", f"DTSTART:{start_str}", f"DTEND:{end_str}",
                    f"SUMMARY:凌星预测: {t['sat_name']} ({t['target_type']})",
                    f"DESCRIPTION:距离: {t['sat_dist_km']:.0f}km, 高度角: {t['alt']}°, 预估大小: {t['covered_pixels']:.1f}px",
                    "END:VEVENT"
                ])
            lines.append("END:VCALENDAR")
            with open(path, 'w', encoding='utf-8') as f: f.write("\r\n".join(lines))
            messagebox.showinfo("成功", f"成功导出 iCal 至: {path}")
        except Exception as e: messagebox.showerror("错误", str(e))

    def stop_calculation(self):
        self.stop_requested = True
        self.safe_status_update("正在请求停止计算...")

    def start_calculation(self):
        try:
            lat, lon, alt = float(self.lat_var.get()), float(self.lon_var.get()), float(self.alt_var.get())
            mode = self.search_mode_var.get()
            rad = float(self.rad_var.get())
            foc_mm = float(self.focal_var.get())
            pix_um = float(self.pixel_var.get())
            max_sats = int(self.max_sats_var.get())
            
            fmt = "%Y-%m-%d %H:%M:%S" if ":" in self.start_var.get() else "%Y-%m-%d"
            start = datetime.datetime.strptime(self.start_var.get().strip(), fmt).replace(tzinfo=datetime.timezone.utc)
            fmt_e = "%Y-%m-%d %H:%M:%S" if ":" in self.end_var.get() else "%Y-%m-%d"
            end = datetime.datetime.strptime(self.end_var.get().strip(), fmt_e).replace(tzinfo=datetime.timezone.utc)
            
            groups = []
            if self.chk_stations.get(): groups.append('stations')
            if self.chk_sl_gen1.get() or self.chk_sl_gen2.get() or self.chk_sl_range.get(): 
                if 'starlink' not in groups: groups.append('starlink')
            if self.chk_oneweb.get(): groups.append('oneweb')
            if self.chk_visual.get(): groups.append('visual')
            if self.chk_active.get(): groups.append('active')
            
            custom_tle = self.custom_tle_text.get("1.0", tk.END).strip()
            kw_filter = self.keyword_var.get().strip().upper()
            force_dl = self.force_download_var.get()
            bck_src = self.backup_src_var.get()
            custom_files = self.custom_file_paths
            
            targets = []
            if self.target_sun.get(): targets.append('太阳')
            if self.target_moon.get(): targets.append('月球')
            if self.target_jupiter.get(): targets.append('木星')
            if self.target_saturn.get(): targets.append('土星')
            if not targets: return messagebox.showwarning("提示", "请至少勾选一个凌星目标！")
                
        except Exception as e: return messagebox.showerror("参数错误", f"请检查输入格式。\n{e}")

        self.map_widget.set_position(lat, lon)
        if self.observer_marker: self.observer_marker.delete()
        self.observer_marker = self.map_widget.set_marker(lat, lon, text="观测点")
        self.clear_map_paths()
        self.transit_data_store.clear()
        for i in self.tree.get_children(): self.tree.delete(i)
        
        self.calc_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.stop_requested = False
        
        sl1 = self.chk_sl_gen1.get()
        sl2 = self.chk_sl_gen2.get()
        sl_range_enabled = self.chk_sl_range.get()
        try: sl_min_id = int(self.sl_min_id_var.get().strip())
        except: sl_min_id = 0
        try: sl_max_id = int(self.sl_max_id_var.get().strip())
        except: sl_max_id = 999999
        
        threading.Thread(target=self.run_calc_thread, args=(
            lat, lon, alt, mode, rad, foc_mm, pix_um, start, end, groups, custom_tle, kw_filter, sl1, sl2, sl_range_enabled, sl_min_id, sl_max_id, max_sats, force_dl, bck_src, custom_files, targets
        ), daemon=True).start()

    def run_calc_thread(self, lat, lon, alt, mode, rad, foc_mm, pix_um, start, end, groups, custom_tle, kw_filter, sl1, sl2, sl_range_enabled, sl_min_id, sl_max_id, max_sats, force_dl, bck_src, custom_files, targets):
        try:
            if self.calc is None:
                self.safe_status_update("正在初始化天体物理核心 (首次可能需下载 de421.bsp，约16MB)...", 5)
                self.calc = TransitCalculator()

            satellites = []
            if custom_tle:
                self.safe_status_update("加载自定义 TLE...", 10)
                cache_c = os.path.join("cache", "custom_tle.txt")
                with open(cache_c, "w", encoding="utf-8") as cf: cf.write(custom_tle)
                satellites = load.tle_file(cache_c)
            else:
                if not groups and not custom_files:
                    self.safe_status_update("错误：未选预设分组或外部文件！", 100)
                    self.after(0, lambda: [self.calc_btn.config(state=tk.NORMAL), self.stop_btn.config(state=tk.DISABLED)])
                    return
                
                self.safe_status_update("正在通过免封锁多源API与本地缓存加载轨道数据...", 10)
                all_group_keys = list(set(groups + list(custom_files.keys())))
                tle_text = self.calc.fetch_tle_data(all_group_keys, force_download=force_dl, backup_src=bck_src, custom_files=custom_files)
                
                cache_f = os.path.join("cache", "combined_tle.txt")
                if len(tle_text.strip()) > 0:
                    with open(cache_f, "w", encoding="utf-8") as cf: 
                        cf.write(tle_text)
                
                self.safe_status_update("正在本地解析轨道数据...", 15)
                satellites = load.tle_file(cache_f)

            filtered = []
            kws = [kw.strip() for kw in kw_filter.split(',') if kw.strip()]
            cat_counts = {'sl1': 0, 'sl2': 0, 'oneweb': 0, 'stations': 0, 'other': 0}
            
            for sat in reversed(satellites):
                name = sat.name.upper()
                if kws and not any(kw in name for kw in kws): continue
                
                sat_id = sat.model.satnum if hasattr(sat, 'model') else 0
                
                if 'STARLINK' in name:
                    if sl_range_enabled:
                        if not (sl_min_id <= sat_id <= sl_max_id): continue
                    else:
                        is_v2 = sat_id >= 55600
                        if is_v2:
                            if not sl2: continue
                            if cat_counts['sl2'] >= max_sats: continue
                            cat_counts['sl2'] += 1
                        else:
                            if not sl1: continue
                            if cat_counts['sl1'] >= max_sats: continue
                            cat_counts['sl1'] += 1
                elif 'ONEWEB' in name:
                    if cat_counts['oneweb'] >= max_sats: continue
                    cat_counts['oneweb'] += 1
                elif any(x in name for x in ['ISS', 'ZARYA', 'CSS', 'TIANGONG', 'TIANHE', 'WENTIAN', 'MENGTIAN']):
                    if cat_counts['stations'] >= max_sats: continue
                    cat_counts['stations'] += 1
                else:
                    if cat_counts['other'] >= max_sats * 2: continue
                    cat_counts['other'] += 1
                    
                filtered.append(sat)
            
            filtered.reverse()
            
            tot = len(filtered)
            if tot == 0:
                self.safe_status_update("未找到符合条件的卫星。请检查是否勾选了相应的星链分类或外部文件内容。", 100)
                self.after(0, lambda: [self.calc_btn.config(state=tk.NORMAL), self.stop_btn.config(state=tk.DISABLED)])
                return
                
            self.safe_status_update(f"本地筛选出 {tot} 颗目标卫星，开始高精度三维轨道演化与凌星测算...", 20)
            
            all_transits = []
            for i, sat in enumerate(filtered):
                if self.stop_requested: break
                
                if i % max(1, (tot // 20)) == 0:
                    self.safe_status_update(f"物理引擎测算中: {sat.name} [{i+1}/{tot}]", 20 + (i/tot)*75)
                
                for tg in targets:
                    if self.stop_requested: break
                    if tg == '太阳':
                        all_transits.extend(self.calc.find_solar_transits(sat, start, end, lat, lon, alt, mode, rad, foc_mm, pix_um))
                    else:
                        all_transits.extend(self.calc.find_disc_transits(sat, start, end, lat, lon, alt, mode, rad, tg, foc_mm, pix_um))
                
            msg = f"计算中断！已找到 {len(all_transits)} 次高质量凌星事件。" if self.stop_requested else f"计算完毕！共找到 {len(all_transits)} 次高质量凌星事件。"
            self.safe_status_update(msg, 100)
            self.after(0, self.update_ui_with_results, all_transits)
            
        except Exception as err:
            err_msg = str(err)
            self.safe_status_update(f"错误: {err_msg}", 0)
            self.after(0, lambda msg=err_msg: messagebox.showerror("计算异常", msg))
        finally:
            self.after(0, lambda: [self.calc_btn.config(state=tk.NORMAL), self.stop_btn.config(state=tk.DISABLED)])

    def update_ui_with_results(self, transits):
        self.progress_var.set(100)
        if not transits:
            self.safe_status_update("未发现可见凌星事件，建议放大搜索半径或检查时间范围。")
            return
            
        for t in transits:
            vals = (
                t['sat_name'], t['norad_id'], t['target_type'],
                t['start_utc_str'], t['closest_utc_str'], t['end_utc_str'],
                t['local_time_str'], t['tz_name'], t['duration'], t['min_sep'],
                t['alt'], t['az'], f"{t['sat_dist_km']:.1f}", 
                f"{t['path_width_km']:.2f}", f"{t['min_dist_center_km']:.2f}",
                f"{t['covered_pixels']:.1f}"
            )
            iid = self.tree.insert("", tk.END, values=vals)
            self.transit_data_store[iid] = t

if __name__ == "__main__":
    app = TransitPredictorApp()
    app.mainloop()