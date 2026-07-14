"""
DATA ENGINE — Scraper Payload Validation Models
Pydantic models for every social/search platform API response.

If a platform changes its payload shape, validation fails gracefully:
  - Raw payload is saved to the dead-letter queue
  - Dashboard alert is fired
  - Worker pipeline continues without crashing

One model per platform. All fields are Optional so partial responses
are captured rather than dropped entirely.
"""

from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field, field_validator, model_validator


# ─────────────────────────────────────────────
# Base Result — all validated items normalise to this
# ─────────────────────────────────────────────

class ScrapedItem(BaseModel):
    """Normalised output for every scraper result."""
    platform:        str
    title:           str = ""
    url:             str = ""
    snippet:         str = ""
    published_at:    str | None = None
    raw_content:     str = ""
    relevance_score: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata:        dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "snippet", "raw_content", mode="before")
    @classmethod
    def truncate_strings(cls, v: Any) -> str:
        if v is None:
            return ""
        return str(v)[:2000]

    @field_validator("relevance_score", mode="before")
    @classmethod
    def clamp_score(cls, v: Any) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 1.0


# ─────────────────────────────────────────────
# Hacker News — Algolia API
# ─────────────────────────────────────────────

class HackerNewsHit(BaseModel):
    objectID:    str | None = None
    title:       str | None = None
    url:         str | None = None
    story_text:  str | None = None
    points:      int | None = None
    num_comments:int | None = None
    author:      str | None = None
    created_at:  str | None = None

class HackerNewsResponse(BaseModel):
    hits: list[HackerNewsHit] = Field(default_factory=list)

    @field_validator("hits", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


# ─────────────────────────────────────────────
# Reddit — OAuth2 API
# ─────────────────────────────────────────────

class RedditPostData(BaseModel):
    title:       str | None = None
    permalink:   str | None = None
    selftext:    str | None = None
    score:       int | None = None
    num_comments:int | None = None
    subreddit:   str | None = None

class RedditChild(BaseModel):
    data: RedditPostData = Field(default_factory=RedditPostData)

class RedditListing(BaseModel):
    children: list[RedditChild] = Field(default_factory=list)

class RedditResponse(BaseModel):
    data: RedditListing = Field(default_factory=RedditListing)


# ─────────────────────────────────────────────
# X / Twitter — API v2
# ─────────────────────────────────────────────

class TwitterPublicMetrics(BaseModel):
    like_count:     int = 0
    retweet_count:  int = 0
    reply_count:    int = 0
    quote_count:    int = 0
    impression_count: int = 0

    @field_validator("*", mode="before")
    @classmethod
    def default_zero(cls, v: Any) -> int:
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

class TwitterTweet(BaseModel):
    id:             str | None = None
    text:           str | None = None
    created_at:     str | None = None
    author_id:      str | None = None
    public_metrics: TwitterPublicMetrics = Field(default_factory=TwitterPublicMetrics)

class TwitterUser(BaseModel):
    id:       str | None = None
    username: str | None = None

class TwitterIncludes(BaseModel):
    users: list[TwitterUser] = Field(default_factory=list)

class TwitterResponse(BaseModel):
    data:     list[TwitterTweet] = Field(default_factory=list)
    includes: TwitterIncludes    = Field(default_factory=TwitterIncludes)

    @field_validator("data", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


# ─────────────────────────────────────────────
# Facebook — Graph API
# ─────────────────────────────────────────────

class FacebookShares(BaseModel):
    count: int = 0

class FacebookPost(BaseModel):
    id:            str | None = None
    message:       str | None = None
    story:         str | None = None
    created_time:  str | None = None
    permalink_url: str | None = None
    shares:        FacebookShares = Field(default_factory=FacebookShares)

class FacebookResponse(BaseModel):
    data: list[FacebookPost] = Field(default_factory=list)

    @field_validator("data", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


# ─────────────────────────────────────────────
# Instagram — Graph API
# ─────────────────────────────────────────────

class InstagramMedia(BaseModel):
    id:             str | None = None
    caption:        str | None = None
    permalink:      str | None = None
    timestamp:      str | None = None
    like_count:     int | None = None
    comments_count: int | None = None

class InstagramMediaResponse(BaseModel):
    data: list[InstagramMedia] = Field(default_factory=list)

    @field_validator("data", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []

class InstagramHashtag(BaseModel):
    id: str | None = None

class InstagramHashtagResponse(BaseModel):
    data: list[InstagramHashtag] = Field(default_factory=list)


# ─────────────────────────────────────────────
# TikTok — Research API
# ─────────────────────────────────────────────

class TikTokVideo(BaseModel):
    id:                str | None = None
    title:             str | None = None
    video_description: str | None = None
    create_time:       int | None = None
    like_count:        int | None = None
    share_count:       int | None = None
    view_count:        int | None = None
    author_name:       str | None = None

class TikTokData(BaseModel):
    videos: list[TikTokVideo] = Field(default_factory=list)

    @field_validator("videos", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []

class TikTokResponse(BaseModel):
    data: TikTokData = Field(default_factory=TikTokData)


# ─────────────────────────────────────────────
# Snapchat — Public Stories API
# ─────────────────────────────────────────────

class SnapchatStory(BaseModel):
    id:          str | None = None
    title:       str | None = None
    description: str | None = None
    shareUrl:    str | None = None
    timestamp:   str | None = None
    publisher:   str | None = None

class SnapchatResponse(BaseModel):
    stories: list[SnapchatStory] = Field(default_factory=list)

    @field_validator("stories", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


# ─────────────────────────────────────────────
# YouTube — Data API v3
# ─────────────────────────────────────────────

class YouTubeThumbnail(BaseModel):
    url: str | None = None

class YouTubeThumbnails(BaseModel):
    default: YouTubeThumbnail = Field(default_factory=YouTubeThumbnail)

class YouTubeSnippet(BaseModel):
    title:        str | None = None
    description:  str | None = None
    channelTitle: str | None = None
    channelId:    str | None = None
    publishedAt:  str | None = None
    thumbnails:   YouTubeThumbnails = Field(default_factory=YouTubeThumbnails)

class YouTubeVideoId(BaseModel):
    videoId: str | None = None

class YouTubeItem(BaseModel):
    id:      YouTubeVideoId = Field(default_factory=YouTubeVideoId)
    snippet: YouTubeSnippet = Field(default_factory=YouTubeSnippet)

class YouTubeResponse(BaseModel):
    items: list[YouTubeItem] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


# ─────────────────────────────────────────────
# LinkedIn — Marketing API
# ─────────────────────────────────────────────

class LinkedInShareCommentary(BaseModel):
    text: str | None = None

class LinkedInShareContent(BaseModel):
    shareCommentary: LinkedInShareCommentary = Field(default_factory=LinkedInShareCommentary)

class LinkedInSpecificContent(BaseModel):
    model_config = {"populate_by_name": True}
    share_content: LinkedInShareContent = Field(
        default_factory=LinkedInShareContent,
        alias="com.linkedin.ugc.ShareContent"
    )

class LinkedInShare(BaseModel):
    id:              str | None = None
    specificContent: LinkedInSpecificContent = Field(default_factory=LinkedInSpecificContent)

class LinkedInResponse(BaseModel):
    elements: list[LinkedInShare] = Field(default_factory=list)

    @field_validator("elements", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


# ─────────────────────────────────────────────
# Google Custom Search
# ─────────────────────────────────────────────

class GoogleSearchItem(BaseModel):
    title:       str | None = None
    link:        str | None = None
    snippet:     str | None = None
    displayLink: str | None = None
    pagemap:     dict[str, Any] = Field(default_factory=dict)

class GoogleSearchResponse(BaseModel):
    items: list[GoogleSearchItem] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def ensure_list(cls, v: Any) -> list:
        return v if isinstance(v, list) else []


# ─────────────────────────────────────────────
# Bing Web Search
# ─────────────────────────────────────────────

class BingWebPage(BaseModel):
    name:            str | None = None
    url:             str | None = None
    snippet:         str | None = None
    displayUrl:      str | None = None
    dateLastCrawled: str | None = None
    language:        str | None = None

class BingWebPages(BaseModel):
    value: list[BingWebPage] = Field(default_factory=list)

class BingSearchResponse(BaseModel):
    webPages: BingWebPages = Field(default_factory=BingWebPages)
