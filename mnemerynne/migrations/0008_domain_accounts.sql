-- Users can be linked to an on-premises domain account (sAMAccountName, e.g.
-- jsmith). Admin rights, and later share permissions, follow that account's AD
-- groups. NULL = not linked.
ALTER TABLE users ADD COLUMN ad_account TEXT COLLATE NOCASE;
CREATE UNIQUE INDEX users_ad_account ON users(ad_account) WHERE ad_account IS NOT NULL;
