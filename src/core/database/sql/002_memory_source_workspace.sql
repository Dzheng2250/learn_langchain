ALTER TABLE agent_memory_sources
ADD COLUMN workspace_id UUID;

UPDATE agent_memory_sources source
SET workspace_id = memory.workspace_id
FROM agent_memories memory
WHERE source.memory_id = memory.id;

ALTER TABLE agent_memory_sources
ALTER COLUMN workspace_id SET NOT NULL;

ALTER TABLE agent_messages
ADD CONSTRAINT uq_agent_messages_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE agent_memories
ADD CONSTRAINT uq_agent_memories_workspace_id UNIQUE (workspace_id, id);

ALTER TABLE agent_memory_sources
ADD CONSTRAINT fk_agent_memory_sources_memory_workspace
FOREIGN KEY (workspace_id, memory_id)
REFERENCES agent_memories(workspace_id, id)
ON DELETE CASCADE;

ALTER TABLE agent_memory_sources
ADD CONSTRAINT fk_agent_memory_sources_message_workspace
FOREIGN KEY (workspace_id, message_id)
REFERENCES agent_messages(workspace_id, id)
ON DELETE CASCADE;
