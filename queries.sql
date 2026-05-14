-- Q1: Top Inventors – who has the most patents?
SELECT
    i.name AS inventor_name,
    COUNT(DISTINCT r.patent_id) AS patent_count
FROM patent_relationships r
JOIN inventors i ON r.inventor_id = i.inventor_id
WHERE i.inventor_id != 'UNKNOWN'
GROUP BY i.name
ORDER BY patent_count DESC
LIMIT 10;

-- Q2: Top Companies – which companies own the most patents?
SELECT
    c.name AS company_name,
    COUNT(DISTINCT r.patent_id) AS patent_count
FROM patent_relationships r
JOIN companies c ON r.company_id = c.company_id
WHERE c.company_id != 'UNKNOWN'
GROUP BY c.name
ORDER BY patent_count DESC
LIMIT 10;

-- Q3: Countries – which countries produce the most patents?
SELECT
    i.country,
    COUNT(DISTINCT r.patent_id) AS patent_count,
    ROUND(COUNT(DISTINCT r.patent_id) * 100.0 / (SELECT COUNT(DISTINCT patent_id) FROM patents), 2) AS share_percent
FROM patent_relationships r
JOIN inventors i ON r.inventor_id = i.inventor_id
WHERE i.country != 'Unknown' AND i.inventor_id != 'UNKNOWN'
GROUP BY i.country
ORDER BY patent_count DESC
LIMIT 10;

-- Q4: Trends Over Time – patents per year
SELECT
    year,
    COUNT(*) AS patent_count
FROM patents
WHERE year IS NOT NULL
GROUP BY year
ORDER BY year;

-- Q5: JOIN Query – combine patents with inventors and companies (sample)
SELECT
    p.patent_id,
    p.title,
    i.name AS inventor_name,
    c.name AS company_name
FROM patents p
JOIN patent_relationships r ON p.patent_id = r.patent_id
JOIN inventors i ON r.inventor_id = i.inventor_id
JOIN companies c ON r.company_id = c.company_id
LIMIT 100;

-- Q6: CTE Query – prolific inventors (>100 patents) by country
WITH prolific_inventors AS (
    SELECT inventor_id
    FROM patent_relationships
    WHERE inventor_id != 'UNKNOWN'
    GROUP BY inventor_id
    HAVING COUNT(DISTINCT patent_id) > 100
)
SELECT
    i.country,
    COUNT(DISTINCT pi.inventor_id) AS num_prolific_inventors,
    SUM(pi.patent_count) AS total_patents_by_them
FROM prolific_inventors pi2
JOIN inventors i ON pi2.inventor_id = i.inventor_id
JOIN (
    SELECT inventor_id, COUNT(DISTINCT patent_id) AS patent_count
    FROM patent_relationships
    GROUP BY inventor_id
) pi ON pi2.inventor_id = pi.inventor_id
GROUP BY i.country
ORDER BY num_prolific_inventors DESC;

-- Q7: Ranking Query – rank inventors by patent count using window function
SELECT
    i.name AS inventor_name,
    COUNT(DISTINCT r.patent_id) AS patent_count,
    RANK() OVER (ORDER BY COUNT(DISTINCT r.patent_id) DESC) AS rank
FROM patent_relationships r
JOIN inventors i ON r.inventor_id = i.inventor_id
WHERE i.inventor_id != 'UNKNOWN'
GROUP BY i.name
ORDER BY rank;
