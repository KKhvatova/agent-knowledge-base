# AI-SDLC: how AI was used to build this

This log records, for each phase, which AI tools were used, how, and **where the AI was wrong or needed correction**.

## Tools

- **Claude Code** (CLI): design interview, implementation, test generation, review.
- **Claude in Chrome**: capturing the public ShopFully website snapshot used as a POC source.

## 1. Design

- **Grilling session** (Claude Code, `grill-me` skill): before writing anything, the AI interviewed me through a design tree, one round of questions at a time, each with a recommended answer. I accepted, changed or rejected each recommendation. Decisions that came out of it are recorded as ADRs in [`decisions/`](decisions/).
- Human decisions vs. AI suggestions: _TODO, for example: the observer agent and the requirement for data versioning/rollback were my additions; the AWS mapping was proposed by the AI and accepted._
- Where the AI fell short: _TODO, for example: it could not read some reference articles (Medium returned 403) or the ShopFully site (bot protection), so these were handled explicitly._

## 2. Implementation

_TODO_

## 3. Testing

_TODO_

## 4. Review

_TODO_

## Lessons learned

_TODO_
