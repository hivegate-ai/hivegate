-- ====================================
-- SAMPLE DATA FOR AGENT API
-- ====================================

-- This file contains sample data including demo agents that work out of the box.
-- Run this after setting up the database schema.

-- ====================================
-- 0. DEMO PROMPTS (stored locally in PostgreSQL)
-- ====================================
-- These prompts are used by the demo agents below.
-- The default PROMPT_STORAGE_BACKEND=postgres serves these directly from the DB.

INSERT INTO prompts.prompts (id, name, template, description, tags) VALUES
('demo-assistant', 'Demo Assistant',
 'You are a helpful AI assistant called Demo Assistant. Answer questions clearly and concisely. Be friendly and informative.',
 'General-purpose demo assistant — works out of the box',
 '["demo", "agentid:demo-assistant", "agent_name:Demo Assistant"]'),
('demo-researcher', 'Demo Researcher',
 'You are a research assistant called Demo Researcher. Help users explore topics, summarize information, compare options, and provide well-structured answers with key takeaways.',
 'Research-focused demo agent',
 '["demo", "agentid:demo-researcher", "agent_name:Demo Researcher"]'),
('customer-support-agent', 'Customer Support Agent',
 'You are a customer support agent. Help users resolve issues, answer product questions, and escalate when needed. Be empathetic and solution-oriented.',
 'Handles customer inquiries and support requests',
 '["support", "agentid:customer-support-agent", "agent_name:Customer Support Agent"]'),
('technical-writer-agent', 'Technical Writer',
 'You are a technical writer. Create clear, well-structured documentation, guides, and explanations. Use appropriate formatting and examples.',
 'Creates technical documentation and guides',
 '["documentation", "agentid:technical-writer-agent", "agent_name:Technical Writer"]'),
('data-analyst-agent', 'Data Analyst',
 'You are a data analyst. Help users understand data, identify patterns, suggest analyses, and explain statistical concepts in accessible terms.',
 'Analyzes data and generates insights',
 '["analytics", "agentid:data-analyst-agent", "agent_name:Data Analyst"]')
ON CONFLICT (id) DO NOTHING;

-- ====================================
-- 1. SAMPLE AGENTS
-- ====================================

-- Insert sample agents (prompt_service_id references prompts above)
INSERT INTO agent_info (id, name, description, prompt_service_id, tags, version) VALUES
('demo-assistant', 'Demo Assistant', 'General-purpose AI assistant — works out of the box', 'demo-assistant', '["demo"]', '2.0'),
('demo-researcher', 'Demo Researcher', 'Research and information assistant', 'demo-researcher', '["demo"]', '2.0'),
('customer-support-agent', 'Customer Support Agent', 'Handles customer inquiries and support requests', 'customer-support-agent', '["support", "customer-service"]', '2.0'),
('technical-writer-agent', 'Technical Writer', 'Creates technical documentation and guides', 'technical-writer-agent', '["documentation", "writing"]', '2.0'),
('qa-testing-agent', 'QA Testing Agent', 'Performs quality assurance and testing tasks', 'qa-testing-agent', '["testing", "quality-assurance"]', '2.0'),
('data-analyst-agent', 'Data Analyst', 'Analyzes data and generates insights', 'data-analyst-agent', '["analytics", "data"]', '2.0'),
('web-search-agent', 'Web Search Agent', 'Searches the web for information', 'web-search-agent', '["search", "web", "information"]', '2.0')
ON CONFLICT (id) DO NOTHING;

-- ====================================
-- 2. SAMPLE TEAMS
-- ====================================

-- Insert sample teams
INSERT INTO team_info (id, name, description, version) VALUES
('support-team', 'Customer Support Team', 'A team dedicated to customer support and assistance', '2.0'),
('development-team', 'Development Team', 'A team focused on software development and technical tasks', '2.0'),
('content-team', 'Content Creation Team', 'A team specialized in content creation and documentation', '2.0'),
('research-team', 'Research Team', 'A team for research and data analysis tasks', '2.0')
ON CONFLICT (id) DO NOTHING;

-- ====================================
-- 3. TEAM-AGENT RELATIONSHIPS
-- ====================================

-- Note: When creating teams via API, the request format is:
-- {
--   "id": "support-team",
--   "name": "Customer Support Team",
--   "description": "A team of agents for customer support",
--   "agents": [
--     {
--       "agent_id": "customer-support-agent",
--       "role": "Primary Support",
--       "order_index": 1
--     },
--     {
--       "agent_id": "web-search-agent", 
--       "role": "Information Gatherer",
--       "order_index": 2
--     }
--   ]
-- }

-- Assign agents to support team (with roles and order)
INSERT INTO team_agent (team_id, agent_id, role, order_index) VALUES
('support-team', 'customer-support-agent', 'Primary Support', 1),
('support-team', 'web-search-agent', 'Information Gatherer', 2),
('support-team', 'technical-writer-agent', 'Documentation Support', 3)
ON CONFLICT DO NOTHING;

-- Assign agents to development team (with roles and order)
INSERT INTO team_agent (team_id, agent_id, role, order_index) VALUES
('development-team', 'qa-testing-agent', 'Quality Assurance', 1),
('development-team', 'technical-writer-agent', 'Technical Documentation', 2),
('development-team', 'web-search-agent', 'Research Support', 3)
ON CONFLICT DO NOTHING;

-- Assign agents to content team (with roles, no specific order)
INSERT INTO team_agent (team_id, agent_id, role, order_index) VALUES
('content-team', 'technical-writer-agent', 'Lead Writer', NULL),
('content-team', 'web-search-agent', 'Research Assistant', NULL)
ON CONFLICT DO NOTHING;

-- Assign agents to research team (with order but no roles - demonstrates flexibility)
INSERT INTO team_agent (team_id, agent_id, role, order_index) VALUES
('research-team', 'data-analyst-agent', NULL, 1),
('research-team', 'web-search-agent', NULL, 2),
('research-team', 'technical-writer-agent', NULL, 3)
ON CONFLICT DO NOTHING;

-- ====================================
-- 4. SAMPLE TOKEN USAGE DATA
-- ====================================

-- Insert sample token usage data for testing
INSERT INTO token_usage (agent_id, session_id, user_id, model, prompt_tokens, completion_tokens, total_tokens, is_estimated) VALUES
('customer-support-agent', 'session-001', 'user-123', 'gemini-2.5-pro', 150, 300, 450, false),
('technical-writer-agent', 'session-002', 'user-456', 'gemini-2.5-pro', 200, 500, 700, false),
('qa-testing-agent', 'session-003', 'user-789', 'gemini-2.5-pro', 100, 250, 350, false),
('data-analyst-agent', 'session-004', 'user-123', 'gemini-2.5-pro', 300, 600, 900, false),
('web-search-agent', 'session-005', 'user-456', 'gemini-2.5-pro', 80, 120, 200, true);

-- ====================================
-- VERIFICATION QUERIES
-- ====================================

-- Uncomment these queries to verify the sample data was inserted correctly

-- Check agents
-- SELECT * FROM agent_info WHERE is_active = true ORDER BY name;

-- Check teams
-- SELECT * FROM team_info WHERE is_active = true ORDER BY name;

-- Check team-agent relationships with details
-- SELECT 
--     t.name as team_name,
--     a.name as agent_name,
--     ta.role,
--     ta.order_index
-- FROM team_agent ta
-- JOIN team_info t ON ta.team_id = t.id
-- JOIN agent_info a ON ta.agent_id = a.id
-- WHERE ta.is_active = true AND t.is_active = true AND a.is_active = true
-- ORDER BY t.name, ta.order_index;

-- Check token usage
-- SELECT 
--     agent_id,
--     COUNT(*) as usage_count,
--     SUM(total_tokens) as total_tokens_used,
--     AVG(total_tokens) as avg_tokens_per_request
-- FROM token_usage 
-- GROUP BY agent_id 
-- ORDER BY total_tokens_used DESC;