"""Defaults en huidige versie voor applicatie-instellingen."""

CURRENT_VERSION = 1

DEFAULTS = {
    "version": CURRENT_VERSION,
    "general": {"language": "nl", "theme": "system", "log_level": "INFO",
                "automatic_updates": False, "worker_threads": 1,
                "parallel_downloads": 1},
    "spotify": {"client_id": "", "client_secret": "",
                "redirect_uri": "http://127.0.0.1:8888/callback",
                "playlist_name_template": "{recovery_set}",
                "auto_select_high_confidence": True,
                "confidence_threshold": 95.0,
                "artwork_resolution": "highest", "market": "NL"},
    "youtube": {"api_key": "", "max_candidates": 10,
                "confidence_threshold": 80.0, "preferred_provider": "official_api",
                "prefer_official_channels": True},
    "download": {"download_dir": "downloads/temp",
                 "processed_dir": "downloads/processed",
                 "output_root": "Recovered", "concurrent_downloads": 1,
                 "retries": 3, "timeout_seconds": 1800, "retry_delay_seconds": 5},
    "audio": {"ffmpeg_path": "", "ffprobe_path": "", "output_format": "mp3",
              "bitrate_kbps": 320, "keep_source": True,
              "processing_timeout_seconds": 1800},
    "metadata": {"filename_template": "{artist} - {title}.mp3",
                 "folder_template": "{year}/Week {week}",
                 "collision_policy": "Rename", "write_genre": True,
                 "write_artwork": True, "write_comments": True,
                 "write_track_number": True, "write_year": True,
                 "max_path_length": 240,
                 "artwork_cache": "downloads/artwork_cache"},
}
