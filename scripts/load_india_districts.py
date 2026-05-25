#!/usr/bin/env python3
"""
Load India district reference data into the districts table.

Data source: public domain India census district list (28 states + 8 UTs, ~780 districts).
We embed a curated CSV of districts rather than requiring an external download.
"""
from __future__ import annotations

import asyncio
import io
import sys

sys.path.insert(0, ".")

# Minimal district seed list — major districts across all states
# Format: district_name, state, state_code
INDIA_DISTRICTS_CSV = """district_name,state,state_code
Mumbai,Maharashtra,MH
Pune,Maharashtra,MH
Nagpur,Maharashtra,MH
Thane,Maharashtra,MH
Nashik,Maharashtra,MH
Aurangabad,Maharashtra,MH
Solapur,Maharashtra,MH
Kolhapur,Maharashtra,MH
Delhi,Delhi,DL
New Delhi,Delhi,DL
North East Delhi,Delhi,DL
South Delhi,Delhi,DL
North West Delhi,Delhi,DL
Bengaluru Urban,Karnataka,KA
Bengaluru Rural,Karnataka,KA
Mysuru,Karnataka,KA
Tumakuru,Karnataka,KA
Belagavi,Karnataka,KA
Dakshina Kannada,Karnataka,KA
Dharwad,Karnataka,KA
Udupi,Karnataka,KA
Chennai,Tamil Nadu,TN
Coimbatore,Tamil Nadu,TN
Madurai,Tamil Nadu,TN
Salem,Tamil Nadu,TN
Tiruchirappalli,Tamil Nadu,TN
Tirunelveli,Tamil Nadu,TN
Vellore,Tamil Nadu,TN
Hyderabad,Telangana,TS
Rangareddy,Telangana,TS
Medchal-Malkajgiri,Telangana,TS
Warangal Urban,Telangana,TS
Karimnagar,Telangana,TS
Kolkata,West Bengal,WB
North 24 Parganas,West Bengal,WB
South 24 Parganas,West Bengal,WB
Hooghly,West Bengal,WB
Howrah,West Bengal,WB
Murshidabad,West Bengal,WB
Barddhaman,West Bengal,WB
Lucknow,Uttar Pradesh,UP
Kanpur Nagar,Uttar Pradesh,UP
Agra,Uttar Pradesh,UP
Varanasi,Uttar Pradesh,UP
Allahabad,Uttar Pradesh,UP
Meerut,Uttar Pradesh,UP
Ghaziabad,Uttar Pradesh,UP
Noida (Gautam Buddha Nagar),Uttar Pradesh,UP
Bareilly,Uttar Pradesh,UP
Gorakhpur,Uttar Pradesh,UP
Jaipur,Rajasthan,RJ
Jodhpur,Rajasthan,RJ
Udaipur,Rajasthan,RJ
Kota,Rajasthan,RJ
Ajmer,Rajasthan,RJ
Bikaner,Rajasthan,RJ
Ahmedabad,Gujarat,GJ
Surat,Gujarat,GJ
Vadodara,Gujarat,GJ
Rajkot,Gujarat,GJ
Bhavnagar,Gujarat,GJ
Gandhinagar,Gujarat,GJ
Bhopal,Madhya Pradesh,MP
Indore,Madhya Pradesh,MP
Jabalpur,Madhya Pradesh,MP
Gwalior,Madhya Pradesh,MP
Ujjain,Madhya Pradesh,MP
Patna,Bihar,BR
Gaya,Bihar,BR
Muzaffarpur,Bihar,BR
Bhagalpur,Bihar,BR
Purnia,Bihar,BR
Chandigarh,Chandigarh,CH
Mohali (SAS Nagar),Punjab,PB
Ludhiana,Punjab,PB
Amritsar,Punjab,PB
Jalandhar,Punjab,PB
Patiala,Punjab,PB
Gurugram,Haryana,HR
Faridabad,Haryana,HR
Ambala,Haryana,HR
Rohtak,Haryana,HR
Dehradun,Uttarakhand,UK
Haridwar,Uttarakhand,UK
Nainital,Uttarakhand,UK
Shimla,Himachal Pradesh,HP
Kangra,Himachal Pradesh,HP
Jammu,Jammu and Kashmir,JK
Srinagar,Jammu and Kashmir,JK
Anantnag,Jammu and Kashmir,JK
Baramulla,Jammu and Kashmir,JK
Kupwara,Jammu and Kashmir,JK
Pulwama,Jammu and Kashmir,JK
Poonch,Jammu and Kashmir,JK
Rajouri,Jammu and Kashmir,JK
Kargil,Ladakh,LA
Leh,Ladakh,LA
Guwahati (Kamrup Metro),Assam,AS
Dibrugarh,Assam,AS
Jorhat,Assam,AS
Silchar (Cachar),Assam,AS
Nagaon,Assam,AS
Imphal West,Manipur,MN
Imphal East,Manipur,MN
Churachandpur,Manipur,MN
Kohima,Nagaland,NL
Dimapur,Nagaland,NL
Aizawl,Mizoram,MZ
Shillong (East Khasi Hills),Meghalaya,ML
Agartala (West Tripura),Tripura,TR
Itanagar (Papum Pare),Arunachal Pradesh,AR
Gangtok (East Sikkim),Sikkim,SK
Bhubaneswar (Khordha),Odisha,OD
Cuttack,Odisha,OD
Berhampur (Ganjam),Odisha,OD
Sambalpur,Odisha,OD
Ranchi,Jharkhand,JH
Dhanbad,Jharkhand,JH
Jamshedpur (East Singhbhum),Jharkhand,JH
Raipur,Chhattisgarh,CG
Bilaspur,Chhattisgarh,CG
Durg,Chhattisgarh,CG
Bastar,Chhattisgarh,CG
Thiruvananthapuram,Kerala,KL
Ernakulam,Kerala,KL
Kozhikode,Kerala,KL
Thrissur,Kerala,KL
Malappuram,Kerala,KL
Kannur,Kerala,KL
Kollam,Kerala,KL
Vijayawada (Krishna),Andhra Pradesh,AP
Visakhapatnam,Andhra Pradesh,AP
Guntur,Andhra Pradesh,AP
Tirupati (Chittoor),Andhra Pradesh,AP
Kurnool,Andhra Pradesh,AP
Puducherry,Puducherry,PY
Port Blair (South Andaman),Andaman and Nicobar Islands,AN
Daman,Daman and Diu,DD
Silvassa (Dadra and Nagar Haveli),Dadra and Nagar Haveli,DN
Lakshadweep,Lakshadweep,LD
Palghar,Maharashtra,MH
Navi Mumbai (Thane),Maharashtra,MH
"""


async def load_districts() -> int:
    """Parse the embedded CSV and upsert into the districts table."""
    import csv

    from sqlalchemy import select

    from storage.database import get_session
    from storage.models import District

    reader = csv.DictReader(io.StringIO(INDIA_DISTRICTS_CSV.strip()))
    rows = list(reader)

    count = 0
    async with get_session() as session:
        for row in rows:
            name = row["district_name"].strip()
            state = row["state"].strip()
            code = row["state_code"].strip()

            # Skip if already exists
            exists = await session.execute(
                select(District).where(
                    District.name == name,
                    District.state == state,
                )
            )
            if exists.scalar_one_or_none():
                continue

            district = District(
                name=name,
                state=state,
                state_code=code,
            )
            session.add(district)
            count += 1

    return count


if __name__ == "__main__":
    n = asyncio.run(load_districts())
    print(f"Loaded {n} districts")
