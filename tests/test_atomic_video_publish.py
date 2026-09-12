"""Destination publication tests; fixtures are confined to temporary directories."""
import errno
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from videotranslator.media import final_video


class AtomicVideoPublishTests(unittest.TestCase):
    def test_same_volume_does_not_copy_data(self):
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory)/'source.mp4', Path(directory)/'out.mp4'
            source.write_bytes(b'complete video')
            with mock.patch.object(final_video.shutil, 'copyfile') as copy:
                final_video.publish_video_candidate(str(source), str(destination), lambda _: True)
            copy.assert_not_called()
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_partial_copy_never_exposes_final_name(self):
        self.check_fallback('partial')

    def test_collision_during_copy_preserves_other_file(self):
        self.check_fallback('collision')

    def test_successful_copy_is_validated_before_publication(self):
        self.check_fallback('success')

    def test_rejected_copy_is_not_published(self):
        self.check_fallback('invalid')

    def check_fallback(self, mode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root/'source.mp4', root/'out.mp4'
            source.write_bytes(b'complete video')
            original_link = final_video.os.link
            def link(src, dst):
                if str(src) == str(source):
                    raise OSError(errno.EXDEV, 'different disk')
                return original_link(src, dst)
            def copy(src, dst):
                self.assertFalse(destination.exists())
                Path(dst).write_bytes(b'partial' if mode == 'partial' else Path(src).read_bytes())
                if mode == 'partial':
                    raise OSError('disk full')
                if mode == 'collision':
                    destination.write_bytes(b'other job')
            def validate(path):
                if mode != 'collision':
                    self.assertFalse(destination.exists())
                self.assertEqual(Path(path).read_bytes(), b'complete video')
                return mode != 'invalid'
            with mock.patch.object(final_video.os, 'link', side_effect=link), \
                 mock.patch.object(final_video.shutil, 'copyfile', side_effect=copy):
                if mode == 'success':
                    final_video.publish_video_candidate(str(source), str(destination), validate)
                else:
                    with self.assertRaises((OSError, RuntimeError)):
                        final_video.publish_video_candidate(str(source), str(destination), validate)
            self.assertEqual(source.read_bytes(), b'complete video')
            if mode == 'collision':
                self.assertEqual(destination.read_bytes(), b'other job')
            elif mode == 'success':
                self.assertEqual(destination.read_bytes(), b'complete video')
            else:
                self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob('.vt-video-*')), [])
