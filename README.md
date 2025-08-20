# Sector RRG Analysis

A sophisticated Sector Relative Rotation Graph (RRG) analysis dashboard, developed by Hughes & Company LLC. This advanced tool provides real-time sector rotation analysis using relative strength and momentum calculations to identify leading, lagging, improving, and weakening sectors.

## Overview

The Sector RRG Analysis application is a professional-grade dashboard that enables traders and analysts to visualize sector rotation patterns in real-time. Using advanced relative strength calculations and momentum analysis, it helps identify which sectors are outperforming or underperforming the broader market, providing valuable insights for sector allocation decisions.

## Features

### 🚀 **Real-Time Sector Analysis**
- **Live Data Integration**: Yahoo Finance real-time price feeds
- **Sector Coverage**: 11 major S&P 500 sectors via ETFs
- **Dynamic Updates**: Real-time relative strength calculations
- **Interactive Controls**: Customizable timeframes and parameters

### 📊 **Advanced RRG Calculations**
- **Relative Strength Ratio**: Sector performance vs. benchmark (SPY)
- **Momentum Analysis**: Weighted moving average momentum calculations
- **Quadrant Classification**: Leading, Lagging, Improving, Weakening sectors
- **Customizable Parameters**: Adjustable short/long moving average periods

### 🎯 **Professional Trading Interface**
- **Quadrant Table**: Real-time sector classification with color coding
- **Interactive Charts**: Dynamic RRG scatter plots with sector trails
- **Seasonality Analysis**: Monthly return patterns and historical analysis
- **Responsive Design**: Professional trading dashboard layout

### 📈 **Data Visualization**
- **RRG Scatter Plot**: Visual representation of sector rotation
- **Seasonality Charts**: Monthly return patterns by sector
- **Color-Coded Sectors**: Consistent sector identification
- **Interactive Elements**: Hover information and annotations

## Installation

### Prerequisites
- Python 3.8+
- pip package manager
- Internet connection for live data

### Setup Instructions

1. **Clone the repository:**
```bash
git clone https://github.com/klefebvre6/sector.git
cd sector
```

2. **Install dependencies:**
```bash
pip install -r requirements.txt
```

3. **Run the application:**
```bash
python app_rrg.py
```

4. **Access the dashboard:**
Open your browser and navigate to `http://localhost:8059`

## Project Structure

```
Sector/
├── app_rrg.py                    # Main Sector RRG application (17KB)
├── requirements.txt              # Python dependencies
├── README.md                     # This documentation
└── .gitignore                    # Git ignore rules
```

## Key Components

### **app_rrg.py** - Main Application
- **Dash Web Framework**: Modern, responsive web interface
- **Yahoo Finance Integration**: Real-time market data
- **RRG Calculations**: Advanced relative strength algorithms
- **Interactive Controls**: Dynamic parameter adjustments
- **Professional UI**: Trading-focused design

### **Sector Coverage**
- **XLV**: Health Care
- **XLRE**: Real Estate
- **XLB**: Materials
- **XLU**: Utilities
- **XLP**: Consumer Staples
- **XLF**: Financials
- **XLY**: Consumer Discretionary
- **XLE**: Energy
- **XLI**: Industrials
- **XLC**: Communications
- **XLK**: Technology

### **RRG Algorithm**
- **Relative Strength**: Sector price / Benchmark price
- **Weighted Moving Averages**: Customizable short/long periods
- **Momentum Calculation**: Rate of change in relative strength
- **Quadrant Classification**: Four-quadrant performance analysis

## Usage

### **Dashboard Interface**

1. **Sector Selection**: Choose which sectors to analyze
2. **Data Frequency**: Select daily or weekly data
3. **Smoothing Parameters**: Adjust short/long moving averages
4. **Quadrant Analysis**: View current sector classifications
5. **Seasonality Toggle**: Show/hide monthly return patterns

### **RRG Analysis**

- **Leading Sectors**: High relative strength and momentum
- **Lagging Sectors**: Low relative strength and momentum
- **Improving Sectors**: Low relative strength but rising momentum
- **Weakening Sectors**: High relative strength but falling momentum

### **Seasonality Analysis**

- **Monthly Patterns**: Historical monthly return analysis
- **Aggregation Options**: Mean or median calculations
- **Custom Timeframes**: Adjustable start year
- **Visual Charts**: Bar chart representation

## Technical Specifications

### **Dependencies**
- **Dash**: Web framework for interactive dashboards
- **yfinance**: Yahoo Finance data integration
- **Pandas**: Data manipulation and analysis
- **NumPy**: Numerical computing
- **Plotly**: Interactive charting library

### **Port Configuration**
- **Default Port**: 8059
- **Development Mode**: Debug enabled for development
- **Host**: Localhost (127.0.0.1)

### **Performance Features**
- **LRU Caching**: Efficient data loading and calculations
- **Real-time Updates**: Live market data integration
- **Responsive Design**: Mobile-friendly interface
- **Fast Loading**: Optimized algorithms for quick analysis

## RRG Methodology

### **Relative Strength Calculation**
1. **Price Ratio**: Sector ETF price / SPY price
2. **Short-term WMA**: Weighted moving average (default: 10 periods)
3. **Long-term WMA**: Weighted moving average (default: 30 periods)
4. **RS Ratio**: 100 + 100 * (Short WMA / Long WMA - 1)

### **Momentum Calculation**
1. **RS Ratio WMA**: Weighted moving average of RS Ratio
2. **RS Momentum**: 100 + 100 * (RS Ratio / RS Ratio WMA - 1)

### **Quadrant Classification**
- **Leading**: RS Ratio ≥ 100, RS Momentum ≥ 100
- **Weakening**: RS Ratio ≥ 100, RS Momentum < 100
- **Lagging**: RS Ratio < 100, RS Momentum < 100
- **Improving**: RS Ratio < 100, RS Momentum ≥ 100

## Trading Applications

### **Sector Rotation Strategies**
- **Momentum Trading**: Follow leading sectors
- **Mean Reversion**: Contrarian plays on lagging sectors
- **Trend Following**: Ride improving sector momentum
- **Risk Management**: Avoid weakening sectors

### **Portfolio Allocation**
- **Overweight**: Leading and improving sectors
- **Underweight**: Lagging and weakening sectors
- **Dynamic Rebalancing**: Regular quadrant analysis
- **Risk Diversification**: Sector correlation management

## Data Sources

### **Market Data**
- **Yahoo Finance**: Real-time ETF price data
- **S&P 500 Sectors**: Major sector ETF coverage
- **Historical Data**: Customizable date ranges
- **Live Updates**: Real-time market feeds

### **Calculation Frequency**
- **Daily Data**: Intraday precision for active trading
- **Weekly Data**: Reduced noise for trend analysis
- **Custom Periods**: Adjustable moving average windows
- **Real-time Updates**: Live market data integration

## Performance Features

### **Efficient Algorithms**
- **LRU Caching**: Optimized data loading
- **Vectorized Operations**: Fast NumPy calculations
- **Memory Management**: Efficient data handling
- **Quick Response**: Real-time analysis updates

### **User Experience**
- **Interactive Controls**: Dynamic parameter adjustments
- **Visual Feedback**: Color-coded quadrant classifications
- **Responsive Interface**: Professional trading dashboard
- **Mobile Compatibility**: Cross-device accessibility

## Development

### **Code Structure**
- **Modular Design**: Separated concerns for maintainability
- **Clean Architecture**: Clear separation of UI and business logic
- **Efficient Algorithms**: Optimized RRG calculations
- **Professional Standards**: Production-ready code quality

### **Extensibility**
- **Additional Sectors**: Easy to add new sector ETFs
- **Custom Benchmarks**: Flexible benchmark selection
- **New Metrics**: Framework for additional analysis
- **Data Sources**: Pluggable data provider architecture

## Testing

### **Quality Assurance**
- **Data Validation**: Market data integrity checks
- **Calculation Accuracy**: RRG algorithm verification
- **Performance Testing**: Load and response time analysis
- **User Interface**: Cross-browser compatibility

### **Error Handling**
- **Data Failures**: Graceful handling of market data issues
- **Calculation Errors**: Robust error handling and recovery
- **User Input Validation**: Input parameter validation
- **Fallback Mechanisms**: Alternative data sources when needed

## License

This project is proprietary to Hughes & Company LLC. All rights reserved.

## Contact

For questions, support, or collaboration opportunities:
- **Company**: Hughes & Company LLC
- **Email**: dhughes@hughesandco.ltd
- **Website**: www.hughesandco.ltd

## Disclaimer

This software is for educational and informational purposes only. It does not constitute investment advice. Trading involves substantial risk of loss and is not suitable for all investors. Past performance is not indicative of future results. Sector rotation analysis should be used as part of a comprehensive investment strategy.
