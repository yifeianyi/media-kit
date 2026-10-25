# 定制与重新打包

公开源码允许使用者修改、测试、fork 与重新发行；项目自身适用 MIT。先核对 LICENSE/NOTICE.md 和新引入依赖的许可。分发名 media-kit、导入名 media_kit、CLI media-kit 是本地默认名称，公开发布前核验占用；fork 应给自己的分发名称和版本作清晰区分。

## 仅配置新档位

```python
from dataclasses import replace
from media_kit import Profile

new_config = replace(config, profiles=(Profile('card', 480, quality=85),))
with MediaStore.open(new_config) as store:
    requested = store.rebuild(asset_id, profiles=['card'])
    completed = store.process(asset_id, timeout=10)
```

新配置不自动改历史资产。rebuild 选中的档位切换到当前配置配方，其余档位不变。retry 保持原请求快照。size/quality/method、输出策略与 algorithm_version 的规范 JSON 共同决定 SHA-256 指纹；不同 profile 名称可以使用相同指纹，但任务和结果仍分开。

## 修改实际算法

1. 建立独立源码副本，按[开发指南](development.md) editable 安装。
2. 在 transforms/converter.py 中明确改变一项规则，例如把 Pillow 缩放的 Image.Resampling.LANCZOS 改成 Image.Resampling.BICUBIC。保持原图不可覆盖、完整输出后发布与独立转换契约。
3. 为该实现使用新的 algorithm_version，例如 fork-bicubic-v1；同步公开默认 Profile/TransformRecipe、配置默认与 ALGORITHM_VERSION，或在自己接入配置中始终显式指定新值。不要让新行为仍以 webp-v1 接受新任务。
4. 用有细节的测试图比较像素/输出 hash，新增测试证明行为已改变，并断言新的 recipe 指纹与旧版本不同；旧持久请求仍保存原快照，不把它们错误标成新算法结果。
5. 更新文档与包版本，执行 pytest、mypy、lint、build；在源码树外安装修改后的 wheel，跑完整生命周期与独立转换示例。
6. 对存量资产显式 rebuild。已有旧 URL 仍标识旧配方；新版本指纹进入新路径。不要原地覆盖旧派生文件或手改数据库中的指纹。

以公开入口验证身份变化：

```python
from dataclasses import replace
from media_kit import Profile, recipe_for, fingerprint

old = Profile('card', 480, algorithm_version='webp-v1')
new = replace(old, algorithm_version='fork-bicubic-v1')
assert fingerprint(recipe_for(old)) != fingerprint(recipe_for(new))
```

仅改源码而不改算法身份不能可靠识别缓存失效。若不再支持执行旧算法快照，应在 fork 的升级说明中明确失败/迁移策略，不能声称旧算法的结果可由新代码透明重建。涉及磁盘布局/schema 的修改还必须更新对应兼容版本并设计显式迁移；安装包版本号不能替代存储版本。

## 发行自己的版本

在 pyproject.toml 调整项目版本和必要的分发名称，保持源码版本常量、文档、CLI 与构建资源同版；若修改导入名/命令名，同时更新配置、测试和示例。构建 wheel/sdist 并进行离线资源与干净安装验收。保留适用许可与来源署名，本指南不代替注册表发布授权或账号配置。

完整、隔离的二次开发验收脚本是 `python tools/verify_customization.py`：它复制源码到临时目录，修改转换实现、algorithm_version、分发名称及源码/metadata 版本，同步临时副本的版本断言，安装开发依赖后执行全套 pytest、mypy、Ruff 和格式检查，再构建 wheel/sdist 并核对发行资源。在另一个干净环境非 editable 安装 fork wheel 后，脚本从源码树外验证输出和指纹均区别于原版、与已测试的定制源码一致，公开版本与 metadata 一致，并运行随包生命周期和独立转换示例。每个子命令有 300 秒超时，结果按阶段输出；原工作树保留，流程不上传发行物。
