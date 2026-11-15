(function () {
    const params = new URLSearchParams(location.search);
    const bvid = (params.get('bvid') || '').trim();
    const video = document.getElementById('player');
    const quality = document.getElementById('quality');
    const status = document.getElementById('status');
    const source = document.getElementById('source');
    const title = document.getElementById('title');

    function setStatus(message, isError = false) {
        status.textContent = message;
        status.classList.toggle('error', isError);
    }

    if (!/^BV[0-9A-Za-z]+$/.test(bvid)) {
        setStatus('缺少有效的 BV 号', true);
        return;
    }

    title.textContent = bvid;
    source.href = `https://www.bilibili.com/video/${bvid}`;
    document.title = `${bvid} - B站播放器`;

    if (typeof dashjs === 'undefined') {
        setStatus('播放器脚本加载失败', true);
        return;
    }

    const player = dashjs.MediaPlayer().create();

    function populateQuality() {
        quality.querySelectorAll('option:not(:first-child)').forEach(option => option.remove());
        const list = player.getBitrateInfoListFor('video') || [];
        const seen = new Set();
        list.forEach((info) => {
            const index = Number(info.qualityIndex);
            if (!Number.isFinite(index) || seen.has(index)) return;
            seen.add(index);
            const option = document.createElement('option');
            option.value = String(index);
            option.textContent = `${info.height ? info.height + 'P' : '清晰度 ' + (index + 1)} · ${Math.round((info.bitrate || 0) / 1000)} kbps`;
            quality.appendChild(option);
        });
    }

    player.on(dashjs.MediaPlayer.events.STREAM_INITIALIZED, () => {
        populateQuality();
        setStatus('');
    });
    player.on(dashjs.MediaPlayer.events.ERROR, () => setStatus('播放失败，请刷新重试', true));

    quality.addEventListener('change', () => {
        const value = Number(quality.value);
        if (value < 0) {
            player.updateSettings({ streaming: { abr: { autoSwitchBitrate: { video: true } } } });
            return;
        }
        player.updateSettings({ streaming: { abr: { autoSwitchBitrate: { video: false } } } });
        player.setQualityFor('video', value, true);
    });

    player.initialize(video, `/api/play/${encodeURIComponent(bvid)}/manifest.mpd`, false);
})();
