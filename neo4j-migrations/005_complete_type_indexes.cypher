// 005_complete_type_indexes.cypher
// sketch_id and nodeLabel indexes for every registered type that 001 doesn't cover:
// types added since (threat intel types, technology) and nodeLabel indexes for the
// rest. Nodes are MERGEd on (nodeLabel, sketch_id) and node search filters on both.
// All statements are idempotent (IF NOT EXISTS).
// Kept complete by flowsint-core/tests/test_neo4j_migrations.py.

// sketch_id
CREATE INDEX idx_sketch_id_isp IF NOT EXISTS FOR (n:isp) ON (n.sketch_id);
CREATE INDEX idx_sketch_id_phishingpage IF NOT EXISTS FOR (n:phishingpage) ON (n.sketch_id);
CREATE INDEX idx_sketch_id_phishkit IF NOT EXISTS FOR (n:phishkit) ON (n.sketch_id);
CREATE INDEX idx_sketch_id_registrar IF NOT EXISTS FOR (n:registrar) ON (n.sketch_id);
CREATE INDEX idx_sketch_id_technology IF NOT EXISTS FOR (n:technology) ON (n.sketch_id);
CREATE INDEX idx_sketch_id_threatactor IF NOT EXISTS FOR (n:threatactor) ON (n.sketch_id);

// nodeLabel
CREATE INDEX idx_nodeLabel_affiliation IF NOT EXISTS FOR (n:affiliation) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_alias IF NOT EXISTS FOR (n:alias) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_asn IF NOT EXISTS FOR (n:asn) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_bankaccount IF NOT EXISTS FOR (n:bankaccount) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_breach IF NOT EXISTS FOR (n:breach) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_cidr IF NOT EXISTS FOR (n:cidr) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_credential IF NOT EXISTS FOR (n:credential) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_creditcard IF NOT EXISTS FOR (n:creditcard) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_cryptonft IF NOT EXISTS FOR (n:cryptonft) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_cryptowallettransaction IF NOT EXISTS FOR (n:cryptowallettransaction) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_device IF NOT EXISTS FOR (n:device) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_dnsrecord IF NOT EXISTS FOR (n:dnsrecord) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_document IF NOT EXISTS FOR (n:document) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_file IF NOT EXISTS FOR (n:file) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_gravatar IF NOT EXISTS FOR (n:gravatar) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_isp IF NOT EXISTS FOR (n:isp) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_leak IF NOT EXISTS FOR (n:leak) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_location IF NOT EXISTS FOR (n:location) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_malware IF NOT EXISTS FOR (n:malware) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_message IF NOT EXISTS FOR (n:message) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_phishingpage IF NOT EXISTS FOR (n:phishingpage) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_phishkit IF NOT EXISTS FOR (n:phishkit) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_phrase IF NOT EXISTS FOR (n:phrase) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_port IF NOT EXISTS FOR (n:port) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_registrar IF NOT EXISTS FOR (n:registrar) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_reputationscore IF NOT EXISTS FOR (n:reputationscore) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_riskprofile IF NOT EXISTS FOR (n:riskprofile) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_script IF NOT EXISTS FOR (n:script) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_session IF NOT EXISTS FOR (n:session) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_sslcertificate IF NOT EXISTS FOR (n:sslcertificate) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_technology IF NOT EXISTS FOR (n:technology) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_threatactor IF NOT EXISTS FOR (n:threatactor) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_weapon IF NOT EXISTS FOR (n:weapon) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_webtracker IF NOT EXISTS FOR (n:webtracker) ON (n.nodeLabel);
CREATE INDEX idx_nodeLabel_whois IF NOT EXISTS FOR (n:whois) ON (n.nodeLabel);
