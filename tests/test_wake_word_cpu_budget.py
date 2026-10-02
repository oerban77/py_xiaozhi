from src.audio_processing import wake_word_detect


def test_wake_word_threads_are_capped_for_small_cpus(monkeypatch):
    monkeypatch.setattr(wake_word_detect.os, "cpu_count", lambda: 4)

    class ConfigStub:
        def get_config(self, key, default=None):
            if key == "WAKE_WORD_OPTIONS.NUM_THREADS":
                return 5
            return default

    detector = wake_word_detect.WakeWordDetector()
    detector._load_config(ConfigStub())

    assert detector._num_threads == 2


def test_wake_word_uses_one_thread_on_single_cpu(monkeypatch):
    monkeypatch.setattr(wake_word_detect.os, "cpu_count", lambda: 1)

    class ConfigStub:
        def get_config(self, key, default=None):
            if key == "WAKE_WORD_OPTIONS.NUM_THREADS":
                return 4
            return default

    detector = wake_word_detect.WakeWordDetector()
    detector._load_config(ConfigStub())

    assert detector._num_threads == 1
