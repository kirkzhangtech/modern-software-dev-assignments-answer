# week1

## k-shot issue
非推理型模型处理字符能力有缺陷,一些推理模型(reasoning models)会在内部生成思维链
  - 使用 K-Shot 模式（关键）
    也就是在 System Prompt 中提供多个“输入 -> 输出”的示例，让模型通过模式匹配来完成任务。

    即使做了以上所有优化,mistral-nemo:12b 在这个任务上的成功率仍然不会是 100%。

  - 为什么无法反转httpstatus

  - 结论是：问题大概率不在你的提示词上，而在于 httpstatus 这个测试词恰好命中了 mistral-nemo:12b 最不擅长的字符级反转任务，且现有提示词的“控制力”不足以覆盖这个特定词的复杂结构

  - 为什么偏偏是 httpstatus 不行？
        httpstatus 对模型来说是一个“坏样本”，它同时包含：
        重复字符：t 出现了 3 次，s 出现了 2 次。
        常见 token 前缀：http 和 status 都是高频词，极易被模型当作一个整体来处理。

  - httpstatus 的核心难点在于：多个重复字母 + 高频子词。下面这些词和它“同构”，适合作为测试用例或 few-shot 示例：
        httpserver	revresptth	http 前缀 + 重复 t、e、r
        statustext	txetsutats	status 子词 + 重复 t、s
        teststatus	sutats tset → sutatstset	重复 t、s，两个高频子词
        stresstest	tsets serts → tsetsserts	重复 s、t，stress + test
        httpsstatus	sutatsptth	双 t、双 s，http + status
        statistics	scitsitats	重复 t、s、i
        assessment	tnemssessa	重复 s，双 s 连续
        successful	lufsseccus	重复 s、c，连续 ss

### 还有哪些类似于k-shot的问题
非推理模型的核心困境：K-Shot 从“辅助”变成“干扰”

- 📉K-Shot 在非推理模型上的具体表现
    结合你的场景，这些问题尤为突出：

    模型会“过度拟合”示例的表面特征：非推理模型对示例的依赖是“模式匹配式”的。你给的示例里如果有 http -> ptth，它可能学到的是“http 要变成 ptth”这个固定映射，而不是“反转”这个规则。这就是为什么你加入 teststatus 示例后，它可能仍然无法正确处理 httpstatus——它在试图匹配某个示例，而不是执行规则。

    对示例的“位置”和“顺序”极度敏感：非推理模型存在明显的 “近因偏差”（Recency Bias），即倾向于模仿在提示词末尾出现的示例。如果你把 teststatus -> sutatstset 放在最后，模型可能会倾向于输出类似 sutatstset 的模式，而不是 httpstatus 的正确反转。这也是为什么有时你觉得“加对了示例”，结果却更差了。

    “多数标签偏差”导致输出僵化：如果示例中存在某种共性（比如都以 s 开头，或以 status 结尾），模型会倾向于重复这种表面模式。对于 httpstatus，如果示例里大量出现 status，模型可能会优先输出 status 的某种变体，而忽略了对 http 部分的处理。<模型会学习这些标签中大部分出现的>

    更强的模型，示例反而“教不动”：一项 2025 年的研究发现，对于较新的、能力较强的模型，示例的主要作用已经退化为“对齐输出格式”，而不再能提升其推理能力。模型更多地依赖自身参数中的知识，而不是从你给的示例中“学习”。这意味着，对于 mistral-nemo:12b 这种不算特别强大的非推理模型，你可能陷入两难：示例太少它学不会，示例太多它又“学歪”。

- 🛠️对你的 httpstatus 任务的直接建议
    既然 K-Shot 在非推理模型上如此脆弱，你的策略需要调整：

    放弃“教会它”的幻想，转向“约束它”：不要指望通过更多示例让它理解反转逻辑。你的提示词目标应该是用最少的示例，强行约束输出格式。只保留 1-2 个格式最干净、与 httpstatus 结构差异最大的示例（比如 apple -> elppa），避免它把 status 或 http 当成固定块来匹配。

    利用“近因偏差”反制：把你最希望它模仿的那个示例（比如一个短小的、不含 http/status 子词的示例）放在提示词的最末尾，紧挨着用户输入。

    核心手段仍是“代码兜底”：鉴于非推理模型的随机性，最可靠的方案依然是程序化重试 + 严格比对。把 K-Shot 提示词看作一个“提高首次命中率的工具”，而不是“保证成功的方案”。


- 除了 K-Shot 问题，大模型提示设计中还有几个系统性偏差同样根植于模型的底层机制。它们和 K-Shot 一样，不是你“写错了提示词”，而是模型处理信息的方式导致的固有缺陷。

    🧠 注意力机制导致的偏差

    1. 近因/首因偏差（Recency/Primacy Bias） 
    模型对提示中开头和结尾的信息关注度最高，中间部分容易被忽略。这被称为“迷失在中间”（Lost in the Middle）[6](https://cloud.tencent.cn/developer/article/2634030#1) [20](https://aclanthology.org/2025.emnlp-main.1422.pdf#4#1)。你的 httpstatus 示例之所以失败，部分原因就是关键规则被淹没在冗长的提示词中，而模型更倾向于模仿最后出现的示例。[4](https://aclanthology.org/2025.findings-naacl.431.pdf#4#3) [11](https://aclanthology.org/2025.coling-main.120.pdf#4#3)

    2. 顺序敏感(Order Sensitivity)  
    仅仅改变提示中信息的排列顺序，就能导致超过 14 个百分点的准确率差异[7](https://aclanthology.org/2026.findings-acl.1921/#citeMarkdown) [14](https://browse-export.arxiv.org/pdf/2601.14152#10#1) 。对于多选问答，把“上下文”放在“问题和选项”之前（CQO）比反过来（QOC）表现好得多，因为因果注意力掩码阻止了选项 token 去“看到”后面的上下文。[7](https://aclanthology.org/2026.findings-acl.1921/#citeMarkdown)

    🎭 模型行为模式的"反噬"  

    3. 人设反噬（Persona Backfire）  
    给模型加“你是资深工程师”这类专家人设，在写作、角色扮演类任务上有帮助，但在数学、代码、事实推理类任务上反而会拉低准确率。研究发现，加了专家人设后 MMLU 准确率从 71.6% 降到 68.0%。原因是模型进入“指令执行/角色扮演模式”，挤占了原本用于“事实回忆”的能力，它更关注“如何像专家说话”，而不是“答案本身是否正确”。[8](https://www.sohu.com/a/1000600173_115128?scm=10001.325_13-325_13.0.0-0-0-0-0.5_1334#1)

    4. 过度提示(Over-Prompting)  
    示例或规则太多反而导致性能下降。IEEE 研究证实，在包括 Mistral 在内的模型上，过量领域特定示例会 paradoxically 降低性能。[3](https://ieeexplore.ieee.org/document/11391015#1#1) 提示词超过 500 词、包含超过 10 个规则时，模型容易出现“认知过载”，忽略部分指令或响应变慢[13](https://signalwire.com/blog/why-over-prompting-kills-ai?x-craft-preview=6cLYZaflFP)。你的 6 个示例虽然不算“过量”，但同构示例的堆叠造成了类似效果。

    📏 示例结构本身的偏差  

    5. A-Not-B 偏差  
    当示例中正确答案总是出现在同一个位置（比如所有示例的答案都是“A”），模型会学到“选 A”这个捷径模式，而不是真正推理。随着示例数量增加，模型更倾向于重复这个表面模式，准确率反而下降[17](https://aclanthology.org/2024.findings-emnlp.322.pdf#5#2)。你的示例中，输出首字母总是等于输入尾字母（本身正确），但多个示例以 s/t 结尾，可能让模型学到了“输出大概率以 s/t 开头”的偏见。

    6. 长度偏差(Length Bias)  
    模型倾向于偏好更长或更短的答案，这与正确答案的实际长度无关，而是训练数据中的统计规律。在需要精确输出的任务中，长度偏差可能导致模型“凑字数”或“过度精简”。[18](https://export.arxiv.org/pdf/2605.21491#10#7)

    🔗 工程层面的问题

    7. 集成不匹配（Integration Mismatch）  
    提示词要求模型输出 JSON，但模型多返回一个字段、用了 snake_case 而不是 camelCase、漏了分隔符，导致下游解析器崩溃[9](https://ar5iv.labs.arxiv.org/html/2509.14404#2) [16](https://huggingface.co/buckets/huggingchat/papers-content/tree/2509/2509.14404.md?code=true#2)。即使语义正确，格式不兼容也会造成“静默错误”。(函数的命名方式也会导致错误提高)

    8. 指令遗忘（Instruction Forgetting）  
    在多轮对话或长上下文中，模型会逐渐“忘记”早期的指令。你在开头写的“只输出反转结果”，在生成到后半段时可能已经被注意力稀释掉了。[6](https://cloud.tencent.cn/developer/article/2634030#1) [9](https://ar5iv.labs.arxiv.org/html/2509.14404#2)

    这些偏差的共同根源
    这些问题几乎都指向同一个底层事实：LLM 不是按照你“写”的逻辑来处理信息的，而是按照它训练时形成的注意力模式和统计偏差来“读”的。 你的提示词是一个“建议”，模型的实际行为受制于架构机制。

    回到你的 httpstatus 任务：你同时踩中了近因偏差（最后几个示例主导行为）、人设反噬（如果写了“你是反转专家”）、同构示例的 A-Not-B 效应（示例太像导致模型走捷径），以及长度偏差（模型可能倾向于输出特定长度的结果）。这就是为什么 30 多次里只成功 1 次——不是你的提示词“不够努力”，而是多个系统性偏差叠加的结果。





###  更严重的问题：示例与测试词“同构”
teststatus 和 statustext 这两个示例和 httpstatus 太像了：

text
teststatus  -> sutatstset
statustext  -> txetsutats
httpstatus  -> ???
模型看到前两个，会倾向于模式匹配而不是逐字符反转。它可能学到的是“status 在结尾时，输出里有 sutatst... 这样的片段”，然后试图把 httpstatus 往这个模式里套，结果就是 http 部分被处理错。
(这里我曾经的想法就是尽量保持同构，能够增加模型输出成功的概率，这个想法是错误的)
```markdown
Examples
Input: apple
Output: elppa

Input: hello
Output: olleh

Input: understand
Output: dnatsrednu
```
这三个词的特点是：没有 http、没有 status、没有重复的 t/s 密集模式。模型无法从它们身上“抄近路”，只能被迫执行逐字符反转。

什么是反模式约束？就是不要让它学习我们输入用例

所以什么k-shot问题？
K-Shot 问题不是一个单一的“bug”，而是一类由少样本示例（K-Shot Examples） 引发的系统性偏差。它的核心矛盾是：你给示例本意是“教模型怎么做”，但模型学到的往往不是你想教的东西。

## chain of though

