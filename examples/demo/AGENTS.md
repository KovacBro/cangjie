<!-- cangjie:begin (managed by `cangjie install --target agents`) -->
## 仓颉 / Cangjie — 省 token 的上下文治理

本项目装了仓颉。它把高频短语折叠成单字（无损可逆），并提供定长工作记忆包。

```bash
cangjie trim -- pytest -q          # 裁掉 90% 噪音输出（先用这个）
cangjie run -- <cmd>               # 跑命令，输出先压一遍再进上下文
cangjie compress -i in.md -o out.md
cangjie expand   -i out.md         # 对外输出前必须还原
cangjie state --set goal="..." --set blocker="..." --add decided="..."
cangjie roll  --add "本轮结论" --tag turn-3
cangjie pack  --budget 2000        # 重建工作记忆包，替换冗长历史
```

规则：
1. 字码只用于内部（思考/草稿/工具输出/记忆包）；给用户的回答与交付文件必须 `expand`。
2. 代码块、URL、路径默认不压缩。
3. 上下文结构 = 系统提示 + 密码本(`cangjie block`) + 工作记忆包 + 最近 k 轮 + 本轮输入。
4. 每 5–8 轮重建一次 `pack`，不要每轮重建（会打掉 prompt cache）。
<!-- cangjie:end -->
