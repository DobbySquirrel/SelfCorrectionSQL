SELECT COUNT(DISTINCT T1.School Name) FROM frpm AS T1 INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds WHERE T2.AvgScrMath > 560 AND T1.`Charter Funding Type` = 'Directly funded'
SELECT COUNT(s.CDSCode) FROM frpm AS f JOIN schools AS s ON f.CDSCode = s.CDSCode WHERE s.City = 'Los Angeles' AND f.`Free Meal Count (K-12)` > 500 AND f.`Free Meal Count (K-12)` < 700
SELECT T1.school_name, T1.funding_type      FROM frpm AS T1      INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds      WHERE T1.city = 'Riverside'        AND T2.avgscrmath > 400;
SELECT T2.AdmEmail1 FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.`Charter School (Y/N)` = 1 ORDER BY T1.`Enrollment (K-12)` ASC LIMIT 1
SELECT T2.Phone FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T2.District = 'Fresno Unified' ORDER BY T1.AvgScrRead ASC LIMIT 1
SELECT T2.school, T1.county_name FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode ORDER BY T1.avgscrmath + T1.avgscrread + T1.avgscrwrite ASC LIMIT 1
SELECT T1.City FROM schools AS T1 INNER JOIN frpm AS T2 ON T2.CDSCode = T1.CDSCode WHERE T1.EILCode = 'HS' AND T1.Low_Grade = '9' AND T1.High_Grade = '12' AND T2.NSLP_Provision_Status = 'Lunch Provision 2'
SELECT T1.City, T1.School, T1.LowGrade FROM schools AS T1 WHERE T1.State = 'CA' ORDER BY T1.Latitude ASC LIMIT 1
SELECT COUNT(*) FROM client AS T1 INNER JOIN district AS T2 ON T1.district_id = T2.district_id WHERE T1.gender = 'M' AND T2.A3 = 'North Bohemia'
SELECT T1.client_id FROM client AS T1 INNER JOIN disp AS T2 ON T1.client_id = T2.client_id INNER JOIN trans AS T3 ON T2.account_id = T3.account_id WHERE T2.type = 'DISPONENT' AND T3.operation = 'POPLATEK PO OBRATU'
SELECT T2.account_id FROM loan AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id WHERE STRFTIME('%Y', T1.date) = '1997' AND T2.frequency = 'POPLATEK TYDNE' ORDER BY T1.amount ASC LIMIT 1
SELECT COUNT(*) FROM account AS T1 INNER JOIN district AS T2 ON T1.district_id = T2.district_id WHERE T2.A2 = 'Litomerice' AND STRFTIME('%Y', T1.date) = '1996'
SELECT COUNT(DISTINCT T1.client_id)  FROM client AS T1  INNER JOIN disp AS T2 ON T1.client_id = T2.client_id  LEFT JOIN account AS T3 ON T2.account_id = T3.account_id  LEFT JOIN card AS T4 ON T3.account_id = T4.account_id  WHERE T1.A3 = 'South Bohemia' AND T4.card_id IS NULL
SELECT COUNT(*) FROM card AS T1 INNER JOIN disp AS T2 ON T1.disp_id = T2.disp_id WHERE T1.type = 'gold' AND T2.type = 'OWNER'
SELECT T2.district_id FROM order AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id WHERE T1.order_id = 33333
SELECT T2.client_id FROM order AS T1 INNER JOIN account AS T2 ON T1.account_id = T2.account_id WHERE T1.order_id = 32423
SELECT SUM(l.amount), l.status FROM client c JOIN account a ON c.client_id = a.client_id JOIN loan l ON a.account_id = l.account_id WHERE c.client_id = 992
SELECT T2.account_id FROM client AS T1 INNER JOIN disp AS T2 ON T1.client_id = T2.client_id INNER JOIN district AS T3 ON T1.district_id = T3.district_id WHERE T1.gender = 'F' ORDER BY T1.birth_date ASC, T3.A11 ASC LIMIT 1
SELECT T1.bond_type, CASE WHEN T3.label = '+' THEN 'Yes' ELSE 'No' END AS IsCarcinogenic  FROM bond AS T1  INNER JOIN molecule AS T3 ON T1.molecule_id = T3.molecule_id  WHERE T1.molecule_id = 'TR010'  GROUP BY T1.bond_type  ORDER BY COUNT(T1.bond_type) DESC  LIMIT 1
SELECT T2.element FROM molecule AS T1 INNER JOIN atom AS T2 ON T1.molecule_id = T2.molecule_id WHERE T1.label = 'TR004'
SELECT T2.atom_id2 FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id WHERE T1.element = 's'
SELECT bond.bond_type FROM bond INNER JOIN molecule ON bond.molecule_id = molecule.molecule_id WHERE molecule.molecule_id BETWEEN 'TR000' AND 'TR050'
SELECT CAST(SUM(CASE WHEN T2.bond_type = '#' THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(DISTINCT T1.molecule_id) FROM molecule AS T1 INNER JOIN bond AS T2 ON T1.molecule_id = T2.molecule_id
SELECT CAST(SUM(CASE WHEN T2.bond_type = '=' THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(T2.bond_id) FROM molecule AS T1 INNER JOIN bond AS T2 ON T1.molecule_id = T2.molecule_id WHERE T1.molecule_id = 'TR047'
SELECT CASE WHEN T2.label = '+' THEN 'Carcinogenic' ELSE 'Not Carcinogenic' END AS Result FROM atom AS T1 INNER JOIN molecule AS T2 ON T1.molecule_id = T2.molecule_id WHERE T1.atom_id = 'TR001_1'
SELECT COUNT(*) FROM molecule WHERE label = '+'
SELECT COUNT(*) AS double_bonds, CASE WHEN T1.label = '+' THEN 'Carcinogenic' ELSE 'Not Carcinogenic' END AS carcinogenicity FROM molecule AS T1 INNER JOIN bond AS T2 ON T1.molecule_id = T2.molecule_id WHERE T1.label = 'TR006' AND T2.bond_type = '='
SELECT T1.element FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id WHERE T2.bond_id = 'TR000_2_3'
SELECT COUNT(DISTINCT T3.bond_type) FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id INNER JOIN bond AS T3 ON T2.bond_id = T3.bond_id INNER JOIN molecule AS T4 ON T1.molecule_id = T4.molecule_id WHERE T4.label = 'TR346'
SELECT T4.label FROM atom AS T1 INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id INNER JOIN bond AS T3 ON T2.bond_id = T3.bond_id INNER JOIN molecule AS T4 ON T3.molecule_id = T4.molecule_id WHERE T1.element = 'S' AND T3.bond_type = ' = '
SELECT T1.element, T2.bond_type FROM atom AS T1 INNER JOIN bond AS T2 ON T1.molecule_id = T2.molecule_id WHERE T1.molecule_id = 'TR002'
SELECT DISTINCT a1.atom_id      FROM atom a1      INNER JOIN bond b ON a1.molecule_id = b.molecule_id      INNER JOIN connected c ON b.bond_id = c.bond_id      INNER JOIN atom a2 ON c.atom_id2 = a2.atom_id      WHERE a1.element = 'c' AND b.bond_type = ' = ' AND a2.element = 'c'
SELECT T1.id, T2.text, T1.hasContentWarning FROM cards AS T1 INNER JOIN rulings AS T2 ON T1.uuid = T2.uuid WHERE T1.artist = 'Stephen Daniel'
SELECT DISTINCT T2.language FROM cards AS T1 INNER JOIN foreign_data AS T2 ON T1.uuid = T2.uuid WHERE T1.name = 'Annul' AND T1.number = '29'
SELECT T1.name, T1.totalSetSize FROM sets AS T1 INNER JOIN set_translations AS T2 ON T1.code = T2.setCode WHERE T2.language = 'Italian'
SELECT T2.text FROM cards AS T1 INNER JOIN rulings AS T2 ON T1.uuid = T2.uuid WHERE T1.name = 'Condemn'
SELECT T2.name, T2.colors FROM sets AS T1 INNER JOIN cards AS T2 ON T1.code = T2.setCode WHERE T1.code = 'OGW'
SELECT      CAST(SUM(CASE WHEN isStorySpotlight = 1 AND isTextless = 0 THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(*) AS Percentage,     id FROM      cards WHERE      isStorySpotlight = 1;
SELECT COUNT(*) FROM cards WHERE borderColor = 'borderless' AND edhrecRank > 12000;
SELECT id FROM cards WHERE cardKingdomFoilId IS NOT NULL AND cardKingdomId IS NOT NULL ORDER BY cardKingdomFoilId ASC LIMIT 3
SELECT COUNT(id), id FROM cards WHERE frameEffects = 'extendedart'
SELECT T2.name, T2.type FROM cards AS T1 INNER JOIN foreign_data AS T2 ON T1.uuid = T2.uuid WHERE T1.watermark = 'Abzan'
SELECT T2.releaseDate FROM cards AS T1 INNER JOIN sets AS T2 ON T1.setCode = T2.code WHERE T1.name = 'Ancestor''s Chosen'
SELECT COUNT(*) FROM cards AS T1 INNER JOIN sets AS T2 ON T1.set_code = T2.code WHERE T2.name = 'Coldsnap' AND T1.converted_mana_cost > 5 AND (T1.power = '*' OR T1.power IS NULL)
SELECT T4.text  FROM sets AS T1  INNER JOIN cards AS T2 ON T1.code = T2.setCode  INNER JOIN rulings AS T3 ON T2.uuid = T3.uuid  INNER JOIN foreign_data AS T4 ON T3.uuid = T4.uuid  WHERE T1.name = 'Coldsnap' AND T4.language = 'Italian';
SELECT T2.name FROM cards AS T1 INNER JOIN foreign_data AS T2 ON T1.uuid = T2.uuid WHERE T1.setCode = 'Coldsnap' AND T2.language = 'Italian' ORDER BY T1.convertedManaCost DESC LIMIT 1
SELECT keyruneCode FROM sets WHERE code = 'PKHC'
SELECT T2.artist FROM cards AS T1 INNER JOIN foreign_data AS T2 ON T1.uuid = T2.uuid WHERE T1.side IS NULL ORDER BY T1.convertedManaCost DESC LIMIT 1
SELECT DISTINCT T1.originalReleaseDate, T2.format FROM cards AS T1 INNER JOIN legalities AS T2 ON T1.uuid = T2.uuid WHERE T1.rarity = 'mythic' ORDER BY T1.originalReleaseDate ASC LIMIT 1
SELECT T1.artist, T2.format      FROM cards AS T1      INNER JOIN legalities AS T2 ON T1.uuid = T2.uuid      GROUP BY T1.artist      ORDER BY COUNT(T1.id) ASC      LIMIT 1;
SELECT T1.name      FROM sets AS T1      INNER JOIN set_translations AS T2 ON T1.code = T2.setCode      WHERE T2.language = 'Korean'      INTERSECT      SELECT T1.name      FROM sets AS T1      INNER JOIN set_translations AS T2 ON T1.code = T2.setCode      WHERE T2.language LIKE '%Japanese%'
SELECT Title FROM posts AS P JOIN users AS U ON P.OwnerUserId = U.Id WHERE U.DisplayName = 'csgillespie'
SELECT u.DisplayName      FROM posts p      INNER JOIN users u ON p.OwnerUserId = u.Id      WHERE p.FavoriteCount = (SELECT MAX(FavoriteCount) FROM posts)
SELECT T2.Body FROM tags AS T1 INNER JOIN posts AS T2 ON T1.ExcerptPostId = T2.Id WHERE T1.TagName = 'bayesian'
SELECT T2.Name FROM users AS T1 INNER JOIN badges AS T2 ON T1.Id = T2.UserId WHERE T1.DisplayName = 'SilentGhost'
SELECT DISTINCT T3.DisplayName FROM posts AS T1 INNER JOIN votes AS T2 ON T1.Id = T2.PostId INNER JOIN users AS T3 ON T2.UserId = T3.Id WHERE T1.Title LIKE '%variance%' AND T2.BountyAmount = 50
SELECT COUNT(DISTINCT UserId) FROM badges WHERE Name = 'Supporter' AND STRFTIME('%Y', Date) = '2011'
SELECT COUNT(DISTINCT b.UserId)      FROM badges b      JOIN users u ON b.UserId = u.Id      WHERE u.Location = 'New York, NY'        AND b.Name IN ('Teacher', 'Supporter')
SELECT T2.Title, T1.ViewCount FROM posts AS T1 INNER JOIN postLinks AS T2 ON T1.Id = T2.RelatedPostId WHERE T2.PostId = 61217
SELECT T1.Date FROM badges AS T1 INNER JOIN posts AS T2 ON T1.UserId = T2.OwnerUserId INNER JOIN users AS T3 ON T2.OwnerUserId = T3.Id WHERE T3.Location = 'Rochester, NY'
SELECT Text FROM comments WHERE PostId IN (SELECT Id FROM posts WHERE CreaionDate = '2010-07-19 19:37:33.0')
SELECT COUNT(T2.Id) FROM badges AS T1 INNER JOIN users AS T2 ON T1.UserId = T2.Id WHERE T1.Name = 'Supporter' AND T2.Age BETWEEN 19 AND 65
SELECT COUNT(DISTINCT T2.Id) FROM badges AS T1 INNER JOIN users AS T2 ON T1.UserId = T2.Id WHERE T1.Name = 'Supporter' AND T2.Age > 65
SELECT COUNT(Id) FROM users WHERE Location = 'New York'
SELECT COUNT(*) FROM posts WHERE OwnerDisplayName = 'Daniel Vassallo'
SELECT T2.OwnerDisplayName      FROM posts AS T1      INNER JOIN users AS T2 ON T1.OwnerUserId = T2.Id      WHERE T1.ViewCount = (SELECT MAX(ViewCount) FROM posts)
SELECT u.DisplayName  FROM users u JOIN posts p ON u.Id = p.OwnerUserId JOIN (     SELECT ParentId, MAX(Score) AS MaxScore     FROM posts     WHERE ParentId IS NOT NULL     GROUP BY ParentId ) sub ON p.ParentId = sub.ParentId AND p.Score = sub.MaxScore;
SELECT T2.DisplayName, T2.WebsiteUrl      FROM votes AS T1      INNER JOIN users AS T2 ON T1.UserId = T2.Id      WHERE T1.VoteTypeId = 8 AND T1.BountyAmount = (SELECT MAX(BountyAmount) FROM votes WHERE VoteTypeId = 8)
SELECT T1.Name FROM badges AS T1 INNER JOIN users AS T2 ON T1.UserId = T2.Id WHERE T2.DisplayName = 'Emmett' ORDER BY T1.Date DESC LIMIT 1
SELECT T1.full_name FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id GROUP BY T1.full_name HAVING COUNT(T2.power_id) > 15
SELECT T1.superhero_name, T1.height_cm FROM superhero AS T1 INNER JOIN publisher AS T2 ON T1.publisher_id = T2.id WHERE T2.publisher_name = 'Marvel Comics' ORDER BY T1.height_cm DESC
SELECT AVG(T1.height_cm) FROM superhero AS T1 INNER JOIN publisher AS T2 ON T1.publisher_id = T2.id WHERE T2.publisher_name = 'Marvel Comics'
SELECT T3.publisher_name      FROM superhero AS T1      INNER JOIN hero_attribute AS T2 ON T1.id = T2.hero_id      INNER JOIN publisher AS T3 ON T1.publisher_id = T3.id      WHERE T2.attribute_name = 'Speed'      ORDER BY T2.attribute_value ASC      LIMIT 1
SELECT T2.publisher_name FROM superhero AS T1 INNER JOIN publisher AS T2 ON T1.publisher_id = T2.publisher_id WHERE T1.superhero_name = 'Blue Beetle II'
SELECT T2.colour FROM superhero AS T1 INNER JOIN colour AS T2 ON T1.hair_colour_id = T2.id WHERE T1.height_cm = 185 AND T1.race = 'human'
SELECT CAST(SUM(CASE WHEN T2.publisher_name = 'Marvel Comics' THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(*) FROM superhero AS T1 INNER JOIN publisher AS T2 ON T1.publisher_id = T2.id WHERE T1.height_cm BETWEEN 150 AND 180
SELECT T1.full_name FROM superhero AS T1      INNER JOIN hero_attribute AS T2 ON T1.id = T2.hero_id      INNER JOIN attribute AS T3 ON T2.attribute_id = T3.id      WHERE T3.attribute_name = 'Strength'      ORDER BY T2.attribute_value DESC      LIMIT 1
SELECT DISTINCT T1.eye_colour_id, T1.hair_colour_id, T1.skin_colour_id FROM superhero AS T1 INNER JOIN colour AS T2 ON T1.eye_colour_id = T2.id INNER JOIN colour AS T3 ON T1.hair_colour_id = T3.id INNER JOIN colour AS T4 ON T1.skin_colour_id = T4.id WHERE T1.gender_id = 2 AND T1.publisher_id = 3
SELECT T1.superhero_name FROM superhero AS T1 INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id INNER JOIN superpower AS T3 ON T2.power_id = T3.id WHERE T3.power_name = 'Adaptation'
SELECT T1.superhero_name FROM superhero AS T1      INNER JOIN colour AS T2 ON T1.eye_colour_id = T2.id      INNER JOIN colour AS T3 ON T1.hair_colour_id = T3.id      WHERE T2.colour = 'Blue' AND T3.colour = 'Brown'
SELECT T1.full_name FROM superhero AS T1 INNER JOIN colour AS T2 ON T1.eye_colour_id = T2.id WHERE T2.colour = 'Blue' LIMIT 5
SELECT T2.surname FROM qualifying AS T1 INNER JOIN drivers AS T2 ON T1.driverId = T2.driverId WHERE T1.raceId = 19 AND T1.q2 IS NOT NULL ORDER BY T1.q2 ASC LIMIT 1
SELECT T2.lat, T2.lng FROM races AS T1 INNER JOIN circuits AS T2 ON T1.circuitid = T2.circuitid WHERE T1.name = 'Abu Dhabi Grand Prix'
SELECT T1.nationality FROM drivers AS T1 INNER JOIN qualifying AS T2 ON T1.driverid = T2.driverid WHERE T2.q2 = '01:40' AND T2.raceid = 355
SELECT COUNT(DISTINCT driverId) FROM results WHERE date = '2015-11-29'
SELECT MIN(T1.year) FROM races AS T1 INNER JOIN circuits AS T2 ON T1.circuitid = T2.circuitid WHERE T2.name = 'Singapore Grand Prix'
SELECT CAST(SUM(CASE WHEN T2.position > 1 THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(*) FROM drivers AS T1 INNER JOIN results AS T2 ON T1.driverid = T2.driverid WHERE T1.surname = 'Hamilton' AND T2.year >= 2010
SELECT T3.name FROM drivers AS T1 INNER JOIN results AS T2 ON T1.driverid = T2.driverid INNER JOIN races AS T3 ON T2.raceid = T3.raceid WHERE T1.forename = 'Lewis' AND T1.surname = 'Hamilton'
SELECT T2.time FROM races AS T1 INNER JOIN results AS T2 ON T1.raceid = T2.raceid WHERE T1.year = 2008 AND T1.name = 'Chinese Grand Prix' AND T2.position = 2
SELECT COUNT(DISTINCT T1.driverId) FROM drivers AS T1 INNER JOIN results AS T2 ON T1.driverId = T2.driverId INNER JOIN races AS T3 ON T2.raceId = T3.raceId WHERE T1.nationality = 'British' AND T3.name = 'Australian Grand Prix'
SELECT      ((strftime('%M', T1.time) - strftime('%M', T2.time)) * 60 + (strftime('%S', T1.time) - strftime('%S', T2.time))) * 100 / (strftime('%M', T2.time) * 60 + strftime('%S', T2.time)) AS percentage_faster FROM      results AS T1 JOIN      results AS T2 ON      T1.raceId = T2.raceId WHERE      T1.driverId = (SELECT driverId FROM results WHERE raceId IN (SELECT raceId FROM races WHERE name = 'Australian Grand Prix') ORDER BY points DESC LIMIT 1)     AND T2.driverId = (SELECT driverId FROM results WHERE raceId IN (SELECT raceId FROM races WHERE name = 'Australian Grand Prix') ORDER BY position DESC LIMIT 1);
SELECT T1.forename, T1.surname FROM drivers AS T1 INNER JOIN driverStandings AS T2 ON T1.driverId = T2.driverId WHERE strftime('%Y', T1.dob) > '1975' AND T2.position = 2
SELECT driverRef FROM drivers WHERE nationality = 'German' ORDER BY dob ASC LIMIT 1
SELECT D.driverId, D.code      FROM drivers AS D      JOIN results AS R ON D.driverId = R.driverId      JOIN lapTimes AS LT ON D.driverId = LT.driverId AND R.raceId = LT.raceId      WHERE STRFTIME('%Y', D.dob) = '1971' AND LT.fastestLapTime IS NOT NULL;
SELECT AVG(COUNT(*)) FROM races WHERE date BETWEEN '2000-01-01' AND '2009-12-31' GROUP BY STRFTIME('%Y', date)
SELECT nationality FROM drivers GROUP BY nationality ORDER BY COUNT(*) DESC LIMIT 1
SELECT COUNT(*) FROM results AS T1 INNER JOIN status AS T2 ON T1.statusid = T2.statusid WHERE T1.raceid = (SELECT raceid FROM races WHERE name = 'Canadian Grand Prix') AND T2.status = 'Accident'
SELECT MIN(T2.milliseconds) AS lap_record FROM races AS T1 INNER JOIN lapTimes AS T2 ON T1.raceId = T2.raceId WHERE T1.name = 'Austrian Grand Prix'
SELECT T1.name, COUNT(*) as match_count FROM League AS T1 INNER JOIN Match AS T2 ON T1.id = T2.league_id GROUP BY T1.name ORDER BY match_count DESC LIMIT 1
SELECT team_fifa_api_id FROM Team_Attributes WHERE buildUpPlaySpeed > 50 AND buildUpPlaySpeed < 60
SELECT T2.team_long_name FROM Match AS T1 INNER JOIN Team_Attributes AS T2 ON T1.home_team_api_id = T2.team_api_id WHERE STRFTIME('%Y', T1.date) = '2012' AND T2.buildUpPlayPassing > ( SELECT AVG(buildUpPlayPassing) FROM Team_Attributes WHERE STRFTIME('%Y', date) = '2012' )
SELECT T1.player_name, AVG(T2.heading_accuracy) as avg_heading_accuracy  FROM Player AS T1  INNER JOIN Player_Attributes AS T2 ON T1.player_api_id = T2.player_api_id  WHERE T1.height > 180  GROUP BY T1.player_name  ORDER BY avg_heading_accuracy DESC  LIMIT 10
SELECT T2.heading_accuracy FROM Player AS T1 INNER JOIN Player_Attributes AS T2 ON T1.player_api_id = T2.player_api_id WHERE T1.player_name = 'Francois Affolter' AND T2.date = '2014-09-18 00:00:00'
SELECT T2.potential FROM Player AS T1 INNER JOIN Player_Attributes AS T2 ON T1.player_api_id = T2.player_api_id WHERE T1.player_name = 'Aaron Doran'
SELECT T2.name FROM Country AS T1 INNER JOIN League AS T2 ON T1.id = T2.country_id WHERE T1.name = 'Germany'
SELECT T1.player_name      FROM Player AS T1      INNER JOIN Player_Attributes AS T2 ON T1.player_api_id = T2.player_api_id      WHERE T1.player_name IN ('Alexis', 'Ariel Borysiuk', 'Arouna Kone')      ORDER BY T2.crossing DESC      LIMIT 1
SELECT COUNT(T1.player_api_id) FROM Player AS T1 INNER JOIN Player_Attributes AS T2 ON T1.player_api_id = T2.player_api_id WHERE T1.height > 180 AND T2.volleys > 70
SELECT MAX(T2.chanceCreationPassing), T2.chanceCreationPassingClass FROM Team AS T1 INNER JOIN Team_Attributes AS T2 ON T1.team_api_id = T2.team_api_id WHERE T1.team_long_name = 'Ajax'
SELECT T2.potential FROM Player AS T1 INNER JOIN Player_Attributes AS T2 ON T1.player_api_id = T2.player_api_id WHERE T1.player_name = 'Francesco Parravicini' AND T2.date = '2010-08-30 00:00:00'
SELECT T1.player_name FROM Player AS T1 INNER JOIN Player_Attributes AS T2 ON T1.player_api_id = T2.player_api_id WHERE T2.attacking_work_rate = 'High'
SELECT DISTINCT T1.ID FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE STRFTIME('%Y', T1.Birthday) = '1937' AND T2.`T-CHO` >= 250
SELECT AVG(T2.aCL_IgG) FROM Patient AS T1 INNER JOIN Examination AS T2 ON T1.ID = T2.ID WHERE T1.Admission = '+' AND (strftime('%Y', 'now') - strftime('%Y', T1.Birthday)) >= 50
SELECT COUNT(Laboratory.ID) FROM Patient INNER JOIN Laboratory ON Patient.ID = Laboratory.ID WHERE Patient.`First Date` = '1991-06-13' AND Patient.Diagnosis = 'SJS' AND STRFTIME('%Y', Laboratory.Date) = '1995'
SELECT AVG(T2.ALB) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T1.SEX = 'F' AND T2.PLT > 400 AND T1.Diagnosis = 'SLE'
SELECT CAST(SUM(CASE WHEN T1.SEX = 'F' THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(T1.ID) AS percentage FROM Patient AS T1 INNER JOIN Examination AS T2 ON T1.ID = T2.ID WHERE STRFTIME('%Y', T1.Birthday) = '1980' AND T2.Diagnosis = 'RA'
SELECT T1.ID, CASE WHEN T2.ALPH > 300 THEN 'No' ELSE 'Yes' END AS ALP_Normal  FROM Patient AS T1  INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID  WHERE T1.Birthday = '1982-04-01'
SELECT T1.ID, (T2.ALB BETWEEN 3.5 AND 5.5) AS AlbuminWithinRange FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE STRFTIME('%Y', T1.Birthday) = '1982'
SELECT T2.ID, T2.SEX, T2.Birthday FROM Laboratory AS T1 INNER JOIN Patient AS T2 ON T1.ID = T2.ID WHERE T1.`T-BIL` = (SELECT MAX(`T-BIL`) FROM Laboratory)
SELECT COUNT(DISTINCT T1.ID) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T1.SEX = 'M' AND T1.WBC BETWEEN 3.5 AND 9.0 AND (T2.FG <= 150 OR T2.FG >= 450)
SELECT DISTINCT T1.Diagnosis FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T2.`U-PRO` >= 30
SELECT T2.Birthday FROM Laboratory AS T1 INNER JOIN Patient AS T2 ON T1.ID = T2.ID WHERE T1.RNP NOT IN ('-', '+-') ORDER BY T2.Birthday DESC LIMIT 1
SELECT T1.ID FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T2.SM NOT IN ('negative', '0') ORDER BY T1.Birthday DESC LIMIT 3
SELECT T1.ID FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T2.SSA NOT IN ('negative', '0') ORDER BY T1.First_Date ASC LIMIT 1
SELECT COUNT(DISTINCT T2.ID) FROM Laboratory AS T1 INNER JOIN Patient AS T2 ON T1.ID = T2.ID WHERE T1.IGG BETWEEN 900 AND 2000 AND T2.Admission = '+'
SELECT COUNT(DISTINCT T1.ID) FROM Patient AS T1 INNER JOIN Laboratory AS T2 ON T1.ID = T2.ID WHERE T1.SEX = 'M' AND T2.ALB BETWEEN 3.5 AND 5.5 AND T2.TP BETWEEN 6.0 AND 8.5
SELECT MAX(ANA) FROM Examination WHERE CRE < 1.5
SELECT T3.college FROM member AS T1 INNER JOIN major AS T3 ON T1.link_to_major = T3.major_id WHERE T1.position = 'Vice President'
SELECT remaining FROM budget WHERE amount = (SELECT MAX(amount) FROM budget WHERE category = 'Food')
SELECT SUM(T2.amount) FROM event AS T1 INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event WHERE T1.event_name = 'September Speaker'
SELECT T2.link_to_member FROM event AS T1 INNER JOIN attendance AS T2 ON T1.event_id = T2.link_to_event WHERE T1.event_name = 'October Meeting'
SELECT T2.major_name FROM member AS T1 INNER JOIN major AS T2 ON T1.link_to_major = T2.major_id WHERE T1.phone = '809-555-3360'
SELECT first_name, last_name, email FROM member WHERE position = 'Secretary'
SELECT T2.expense_description FROM member AS T1 INNER JOIN expense AS T2 ON T1.member_id = T2.link_to_member WHERE T1.first_name = 'Sacha' AND T1.last_name = 'Harrison'
SELECT T1.position FROM member AS T1 INNER JOIN major AS T2 ON T1.link_to_major = T2.major_id WHERE T2.major_name = 'Business'
SELECT T2.last_name, T2.position FROM expense AS T1 INNER JOIN member AS T2 ON T1.link_to_member = T2.member_id WHERE T1.expense_description = 'Pizza' AND T1.expense_date = '2019-09-10'
SELECT T2.college FROM member AS T1 INNER JOIN major AS T2 ON T1.link_to_major = T2.major_id WHERE T1.first_name = 'Katy' AND T1.link_to_major = 'rec1N0upiVLy5esTO'
SELECT T1.event_name, T1.location FROM event AS T1 INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event WHERE T2.remaining > 0
SELECT T1.event_name  FROM event AS T1  INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event  INNER JOIN expense AS T3 ON T2.budget_id = T3.link_to_budget  WHERE T3.cost < (SELECT SUM(cost) / COUNT(event_id) FROM expense WHERE link_to_budget IN (SELECT budget_id FROM budget WHERE category = 'Parking'))
SELECT CAST(SUM(T2.cost) AS REAL) * 100 / COUNT(T1.event_id) AS percentage      FROM event AS T1      INNER JOIN budget AS T2 ON T1.event_id = T2.link_to_event      WHERE T1.type = 'Meeting';
SELECT T1.first_name, T1.last_name, T3.college FROM member AS T1 INNER JOIN link_to_major AS T2 ON T1.member_id = T2.member_id INNER JOIN major AS T3 ON T2.major_id = T3.major_id WHERE T1.position = 'Secretary'
SELECT SUM(T1.spent), T3.event_name FROM budget AS T1 INNER JOIN expense AS T2 ON T1.budget_id = T2.link_to_budget INNER JOIN event AS T3 ON T2.link_to_event = T3.event_id WHERE T1.category = 'Speaker Gifts'
SELECT T1.CustomerID FROM yearmonth AS T1 INNER JOIN customers AS T2 ON T1.CustomerID = T2.CustomerID WHERE STRFTIME('%Y', T1.Date) = '2012' AND T2.Segment = 'LAM' ORDER BY T1.Consumption ASC LIMIT 1
SELECT STRFTIME('%Y', T1.Date) AS Year, SUM(T1.Consumption) AS TotalConsumption  FROM yearmonth AS T1  INNER JOIN customers AS T2 ON T1.CustomerID = T2.CustomerID  WHERE T2.Currency = 'CZK'  GROUP BY Year  ORDER BY TotalConsumption DESC  LIMIT 1
SELECT SUM(CASE WHEN CustomerID = 7 THEN Consumption ELSE 0 END) - SUM(CASE WHEN CustomerID = 5 THEN Consumption ELSE 0 END) AS difference FROM yearmonth WHERE Date = 201304
SELECT SUM(T1.Price) FROM transactions_1k AS T1 INNER JOIN customers AS T2 ON T1.CustomerID = T2.CustomerID WHERE T2.Currency = 'EUR'
SELECT T2.GasStationID FROM transactions_1k AS T1 INNER JOIN gasstations AS T2 ON T2.GasStationID = T1.GasStationID GROUP BY T2.GasStationID ORDER BY SUM(T1.Price) DESC LIMIT 1
SELECT DISTINCT T2.CustomerID FROM transactions_1k AS T1 INNER JOIN yearmonth AS T2 ON T1.CustomerID = T2.CustomerID WHERE T1.ProductID = 5 AND T1.Price / T1.Amount > 29.00 AND STRFTIME('%Y%m', T2.Date) = '201208'
