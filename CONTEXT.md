# Doc2Run Agent

Doc2Run Agent turns a user request, one relevant reusable scene, and private API knowledge into a verified Python program through four explicit stages: Chat, Code, Fix, and Memory.

## Language

**Scene**:
A complete reusable description of one concrete or generalizable arrangement, including its components, relationships, invariants, and scaling rules. Exactly one Scene is selected and injected in full during Chat.
_Avoid_: Domain document, scenario chunk, memory example

**Scenario Plan**:
An open-structure Markdown blueprint produced during Chat that makes the requested scene executable by describing its arrangement, components, relationships, parameters, generalization rules, and acceptance conditions. The user-confirmed version is frozen and passed to Code unchanged.
_Avoid_: ImplementationPlan, plan summary, hidden reasoning

**API Knowledge**:
Documentation that explains how code calls available APIs or SDKs, including signatures, parameters, return values, ordering, limits, and setup rules. It is searched only during Code and Fix.
_Avoid_: Domain knowledge, Scene

**Approved Scene**:
A confirmed Scenario Plan whose generated code ran successfully and was accepted by the user. It is saved directly into the Scene library and can be selected by later Chat sessions.
_Avoid_: Memory candidate, approved memory

**Memory stage**:
The final persistence step after user approval. It writes the confirmed Scenario Plan directly to
`domain_knowledge/scenes/`; it does not maintain candidate, rejected, or parallel memory stores.
