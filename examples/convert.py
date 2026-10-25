"""独立文件转换，不创建受管实例、数据库或任务。"""

from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from media_kit import Profile, convert_thumbnail, inspect_image


def main() -> None:
    with TemporaryDirectory(prefix="media-kit-convert-") as directory:
        base = Path(directory)
        source, target = base / "source.png", base / "card.webp"
        Image.new("RGBA", (1000, 600), (0, 128, 255, 128)).save(source)
        before = source.read_bytes()
        info = inspect_image(source)
        result = convert_thumbnail(source, target, Profile("card", 480))
        assert info.width == 1000 and result.width == 480
        assert source.read_bytes() == before
        assert not list(base.rglob("*.sqlite3"))
        print(f"独立转换完成：{result.width}x{result.height}，{result.file_size} bytes")


if __name__ == "__main__":
    main()
