import React, { useState, useEffect, useCallback } from 'react'
import './TutorialPopover.css'

const TIPS: string[] = [
  '💡 输入关键词并点击「收集」，AI 会自动搜索网页、抓取内容并生成知识卡片。',
  '📚 卡片会按主题自动组织成树形结构，点击卡片可查看 AI 生成的详细总结。',
  '📁 使用「会话」功能可以按不同主题分类管理知识收集任务，互不干扰。',
  '🔗 选中卡片后点击紫色 ➕ 按钮，可以从已有卡片出发进行延申搜索，探索更多相关内容。',
  '📊 知识图谱视图可以直观展示卡片之间的链接关系，点击节点即可跳转。',
  '🌐 支持博查、百度、Exa 三种搜索引擎，可在搜索时自由切换，自动过滤低质量域名。',
  '📝 所有卡片以 Markdown 格式存储在服务器，支持双向链接和 Obsidian 风格的知识管理。',
  '🆕 选中卡片后点击紫色边框的 ➕ 按钮，可以创建一张空白卡片并自动链接到当前卡片。',
  '✏️ 选中卡片后点击 ✎ 按钮进入编辑模式，支持修改卡片标题和 Markdown 内容。',
  '🔍 收集面板可以同时运行多个搜索任务，每个任务独立流式输出，互不影响。',
  '⚙️ 在设置页面可以配置自定义 AI 提供者（API URL、密钥和模型），支持 OpenAI 兼容接口。',
  '📱 竖屏模式下底部导航栏支持切换会话、卡片、内容、收集器和图谱五个页面。',
  '🗂️ 卡片列表支持管理模式，可以批量选择并删除卡片，方便清理不需要的内容。',
]

const STORAGE_KEY = 'knowledgeDiver.tutorialClosed'
const TIP_INDEX_KEY = 'knowledgeDiver.tutorialTipIndex'

export const TutorialPopover: React.FC = () => {
  const [visible, setVisible] = useState(false)
  const [tipIndex, setTipIndex] = useState(0)
  const [exiting, setExiting] = useState(false)

  useEffect(() => {
    const closed = localStorage.getItem(STORAGE_KEY)
    if (closed === 'true') return

    const savedIndex = parseInt(localStorage.getItem(TIP_INDEX_KEY) || '0', 10)
    setTipIndex(savedIndex % TIPS.length)

    const timer = setTimeout(() => setVisible(true), 800)
    return () => clearTimeout(timer)
  }, [])

  const handleClose = useCallback(() => {
    setExiting(true)
    setTimeout(() => {
      setVisible(false)
      setExiting(false)
      localStorage.setItem(STORAGE_KEY, 'true')
    }, 250)
  }, [])

  const handleNext = useCallback(() => {
    const next = (tipIndex + 1) % TIPS.length
    setTipIndex(next)
    localStorage.setItem(TIP_INDEX_KEY, String(next))
  }, [tipIndex])

  if (!visible) return null

  return (
    <div className={`tutorial-popover ${exiting ? 'tutorial-popover--exit' : ''}`}>
      <div className="tutorial-popover__body">
        <p className="tutorial-popover__text">{TIPS[tipIndex]}</p>
      </div>
      <div className="tutorial-popover__footer">
        <span className="tutorial-popover__counter">
          {tipIndex + 1} / {TIPS.length}
        </span>
        <div className="tutorial-popover__actions">
          <button
            className="tutorial-popover__btn tutorial-popover__btn--next"
            onClick={handleNext}
          >
            换一条
          </button>
          <button
            className="tutorial-popover__btn tutorial-popover__btn--close"
            onClick={handleClose}
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  )
}
